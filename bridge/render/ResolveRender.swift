// ResolveRender: native text rendering for Resolve AI Bridge (macOS).
//
//   ResolveRender sheet <spec.json>     labelled contact sheet (JPEG)
//   ResolveRender captions <spec.json>  transparent ProRes 4444 caption movie
//
// Homebrew ffmpeg ships without drawtext/libass, so text is drawn here with
// CoreText and video is written with AVFoundation. Prints one JSON object on
// stdout: {"ok":true,...} or {"ok":false,"error":"..."} (non-zero exit).

import AppKit
import AVFoundation
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

struct RenderFailure: Error { let message: String }

func fail(_ message: String) -> Never {
    emit(["ok": false, "error": message])
    exit(1)
}

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    }
}

let srgb = CGColorSpace(name: CGColorSpace.sRGB)!

func color(_ hex: String?, _ fallback: String) -> CGColor {
    var s = (hex ?? fallback).trimmingCharacters(in: .whitespaces)
    if s.hasPrefix("#") { s.removeFirst() }
    guard s.count == 6 || s.count == 8, let v = UInt64(s, radix: 16) else { return color(fallback, "#FFFFFF") }
    let hasAlpha = s.count == 8
    let r = CGFloat((v >> (hasAlpha ? 24 : 16)) & 0xFF) / 255
    let g = CGFloat((v >> (hasAlpha ? 16 : 8)) & 0xFF) / 255
    let b = CGFloat((v >> (hasAlpha ? 8 : 0)) & 0xFF) / 255
    let a = hasAlpha ? CGFloat(v & 0xFF) / 255 : 1
    return CGColor(colorSpace: srgb, components: [r, g, b, a])!
}

func font(named name: String?, size: CGFloat, weight: NSFont.Weight) -> CTFont {
    if let name = name, !name.isEmpty, let f = NSFont(name: name, size: size) { return f as CTFont }
    return NSFont.systemFont(ofSize: size, weight: weight) as CTFont
}

func line(_ text: String, _ font: CTFont, _ fill: CGColor, stroke: CGColor? = nil, strokeWidth: CGFloat = 0) -> CTLine {
    var attrs: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): fill,
    ]
    if let stroke = stroke {
        attrs[NSAttributedString.Key(kCTStrokeColorAttributeName as String)] = stroke
        attrs[NSAttributedString.Key(kCTStrokeWidthAttributeName as String)] = strokeWidth  // positive: stroke only
    }
    return CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attrs))
}

func width(of l: CTLine) -> CGFloat { CGFloat(CTLineGetTypographicBounds(l, nil, nil, nil)) }

func loadSpec(_ path: String) -> [String: Any] {
    guard let data = FileManager.default.contents(atPath: path),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        fail("Could not read the JSON spec at \(path)")
    }
    return obj
}

func num(_ v: Any?, _ fallback: Double) -> Double {
    if let d = v as? Double { return d }
    if let i = v as? Int { return Double(i) }
    if let n = v as? NSNumber { return n.doubleValue }
    return fallback
}

// MARK: - contact sheet

func loadImage(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

func drawLine(_ l: CTLine, in ctx: CGContext, x: CGFloat, baseline: CGFloat) {
    ctx.textPosition = CGPoint(x: x, y: baseline)
    CTLineDraw(l, ctx)
}

func truncated(_ text: String, _ font: CTFont, _ color: CGColor, maxWidth: CGFloat) -> CTLine {
    let full = line(text, font, color)
    if width(of: full) <= maxWidth { return full }
    let ellipsis = line("…", font, color)
    return CTLineCreateTruncatedLine(full, Double(maxWidth), .end, ellipsis) ?? full
}

func renderSheet(_ spec: [String: Any]) {
    guard let output = spec["output"] as? String else { fail("sheet spec needs output") }
    let cells = spec["cells"] as? [[String: Any]] ?? []
    if cells.isEmpty { fail("sheet spec has no cells") }
    let columns = max(1, min(Int(num(spec["columns"], 4)), cells.count))
    let gap: CGFloat = 10, pad: CGFloat = 10
    var cellW = CGFloat(num(spec["cell_width"], 320))
    let maxWidth: CGFloat = 1600
    if CGFloat(columns) * cellW + CGFloat(columns + 1) * gap > maxWidth {
        cellW = floor((maxWidth - CGFloat(columns + 1) * gap) / CGFloat(columns))
    }
    let imgH = round(cellW * 9 / 16)
    let titleSize = max(11, round(cellW * 0.045)), subSize = max(10, round(cellW * 0.038))
    let titleFont = font(named: nil, size: titleSize, weight: .semibold)
    let subFont = font(named: "Menlo-Regular", size: subSize, weight: .regular)
    let badgeFont = font(named: nil, size: max(14, round(cellW * 0.07)), weight: .heavy)
    let textBlock = titleSize * 1.35 + subSize * 1.4 + 6
    let cellH = imgH + 8 + textBlock
    let rows = Int(ceil(Double(cells.count) / Double(columns)))
    let W = Int(CGFloat(columns) * cellW + CGFloat(columns + 1) * gap)
    let H = Int(CGFloat(rows) * cellH + CGFloat(rows + 1) * gap)
    guard let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0, space: srgb,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { fail("Could not allocate the sheet") }
    ctx.setFillColor(color(spec["background"] as? String, "#121212"))
    ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
    let line2a = color("#2a2a2a", "#2a2a2a")
    let ink = color("#f0f0f0", "#f0f0f0"), muted = color("#a5a5a5", "#a5a5a5")
    ctx.interpolationQuality = .high

    for (i, cell) in cells.enumerated() {
        let col = i % columns, row = i / columns
        let x = gap + CGFloat(col) * (cellW + gap)
        let top = gap + CGFloat(row) * (cellH + gap)            // distance from the top edge
        let imgRect = CGRect(x: x, y: CGFloat(H) - top - imgH, width: cellW, height: imgH)
        ctx.setFillColor(color("#000000", "#000000"))
        ctx.fill(imgRect)
        if let path = cell["image"] as? String, let img = loadImage(path) {
            let iw = CGFloat(img.width), ih = CGFloat(img.height)
            let scale = min(imgRect.width / iw, imgRect.height / ih)
            let dw = iw * scale, dh = ih * scale
            ctx.draw(img, in: CGRect(x: imgRect.midX - dw / 2, y: imgRect.midY - dh / 2, width: dw, height: dh))
        }
        ctx.setStrokeColor(line2a)
        ctx.setLineWidth(1)
        ctx.stroke(imgRect.insetBy(dx: 0.5, dy: 0.5))

        // Number badge, top-left of the image.
        let number = "\(cell["number"].map { "\($0)" } ?? "\(i + 1)")"
        let badge = line(number, badgeFont, ink)
        var ascent: CGFloat = 0, descent: CGFloat = 0
        let bw = CGFloat(CTLineGetTypographicBounds(badge, &ascent, &descent, nil))
        let bh = ascent + descent + 8
        let pill = CGRect(x: imgRect.minX + 6, y: imgRect.maxY - 6 - bh, width: max(bh, bw + 14), height: bh)
        ctx.setFillColor(color("#000000C0", "#000000C0"))
        ctx.addPath(CGPath(roundedRect: pill, cornerWidth: bh / 2, cornerHeight: bh / 2, transform: nil))
        ctx.fillPath()
        drawLine(badge, in: ctx, x: pill.midX - bw / 2, baseline: pill.minY + 4 + descent)

        let maxText = cellW - 2
        let title = truncated(cell["title"] as? String ?? "", titleFont, ink, maxWidth: maxText)
        let sub = truncated(cell["subtitle"] as? String ?? "", subFont, muted, maxWidth: maxText)
        let titleBase = imgRect.minY - 8 - titleSize
        drawLine(title, in: ctx, x: x + 1, baseline: titleBase)
        drawLine(sub, in: ctx, x: x + 1, baseline: titleBase - subSize * 1.45)
        _ = pad
    }

    guard let image = ctx.makeImage(),
          let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: output) as CFURL, UTType.jpeg.identifier as CFString, 1, nil)
    else { fail("Could not create \(output)") }
    CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
    if !CGImageDestinationFinalize(dest) { fail("Could not write \(output)") }
    emit(["ok": true, "output": output, "width": W, "height": H, "cells": cells.count])
}

// MARK: - captions

struct Word { let text: String; let start: Int; let end: Int }
struct Cue { let start: Int; let end: Int; let words: [Word] }

final class CaptionRenderer {
    let width: Int, height: Int
    let style: String, position: String
    let baseFont: CTFont
    let fontSize: CGFloat
    let maxWidth: CGFloat
    let textColor: CGColor, highlight: CGColor, outline: CGColor
    let uppercase: Bool
    let fontName: String?

    init(spec: [String: Any], width: Int, height: Int) {
        self.width = width
        self.height = height
        style = (spec["style"] as? String ?? "pop").lowercased()
        position = (spec["position"] as? String ?? "bottom").lowercased()
        fontSize = CGFloat(num(spec["font_size"], Double(height) * 0.065))
        fontName = spec["font"] as? String
        baseFont = font(named: fontName, size: fontSize, weight: .heavy)
        maxWidth = CGFloat(width) * CGFloat(num(spec["max_width_ratio"], 0.8))
        let colors = spec["colors"] as? [String: Any] ?? [:]
        textColor = color(colors["text"] as? String, "#FFFFFF")
        highlight = color(colors["highlight"] as? String, "#FFD43B")
        outline = color(colors["outline"] as? String, "#000000")
        uppercase = spec["uppercase"] as? Bool ?? false
    }

    /// Wrap words into at most two lines, shrinking the font if needed. Returns (font, lines of word indexes).
    func layout(_ cue: Cue) -> (CTFont, [[Int]]) {
        var size = fontSize
        while true {
            let f = size == fontSize ? baseFont : font(named: fontName, size: size, weight: .heavy)
            let space = width(of: line(" ", f, textColor)) * 1.2
            var lines: [[Int]] = [[]]
            var current: CGFloat = 0
            for (i, w) in cue.words.enumerated() {
                let ww = width(of: line(display(w.text), f, textColor))
                if !lines[lines.count - 1].isEmpty && current + space + ww > maxWidth {
                    lines.append([])
                    current = 0
                }
                current += (lines[lines.count - 1].isEmpty ? 0 : space) + ww
                lines[lines.count - 1].append(i)
            }
            if lines.count <= 2 || size < fontSize * 0.55 { return (f, lines) }
            size *= 0.9
        }
    }

    func width(of l: CTLine) -> CGFloat { CGFloat(CTLineGetTypographicBounds(l, nil, nil, nil)) }
    func display(_ s: String) -> String { uppercase ? s.uppercased() : s }

    func draw(cue: Cue, frame: Int, active: Int, into ctx: CGContext) {
        let (f, lines) = layout(cue)
        let size = CTFontGetSize(f)
        let lineHeight = size * 1.18
        let space = width(of: line(" ", f, textColor)) * 1.2
        let blockH = lineHeight * CGFloat(lines.count)
        let centerY: CGFloat
        switch position {
        case "top": centerY = CGFloat(height) * 0.82
        case "center", "middle": centerY = CGFloat(height) * 0.5
        default: centerY = CGFloat(height) * 0.2
        }
        let strokeW = size * 0.14
        for (li, idxs) in lines.enumerated() {
            let lineLines = idxs.map { line(display(cue.words[$0].text), f, textColor) }
            let total = lineLines.map(width(of:)).reduce(0, +) + space * CGFloat(max(0, idxs.count - 1))
            var x = (CGFloat(width) - total) / 2
            let baseline = centerY + blockH / 2 - lineHeight * CGFloat(li + 1) + size * 0.28
            for (k, wi) in idxs.enumerated() {
                let word = cue.words[wi]
                let ww = width(of: lineLines[k])
                var fill = textColor
                var scale: CGFloat = 1
                switch style {
                case "karaoke":
                    if frame >= word.start { fill = highlight }
                case "minimal":
                    fill = wi == active ? textColor : textColor.copy(alpha: 0.82)!
                default:  // pop
                    if wi == active {
                        fill = highlight
                        let t = CGFloat(frame - word.start)
                        if t >= 0 && t < 4 { scale = 1 + 0.12 * sin(.pi * (t + 1) / 5) }
                    }
                }
                let text = display(word.text)
                ctx.saveGState()
                if scale != 1 {
                    let cx = x + ww / 2, cy = baseline + size * 0.3
                    ctx.translateBy(x: cx, y: cy)
                    ctx.scaleBy(x: scale, y: scale)
                    ctx.translateBy(x: -cx, y: -cy)
                }
                // Outline with a soft shadow, then the fill on top.
                ctx.setShadow(offset: CGSize(width: 0, height: -size * 0.05), blur: size * 0.22,
                              color: CGColor(colorSpace: srgb, components: [0, 0, 0, 0.55]))
                ctx.setLineJoin(.round)
                drawLine(line(text, f, outline, stroke: outline, strokeWidth: strokeW / size * 100 * 2), in: ctx, x: x, baseline: baseline)
                ctx.setShadow(offset: .zero, blur: 0, color: nil)
                drawLine(line(text, f, fill), in: ctx, x: x, baseline: baseline)
                if style == "minimal" && wi == active {
                    let bar = CGRect(x: x, y: baseline - size * 0.2, width: ww, height: max(2, size * 0.08))
                    ctx.setFillColor(highlight)
                    ctx.addPath(CGPath(roundedRect: bar, cornerWidth: bar.height / 2, cornerHeight: bar.height / 2, transform: nil))
                    ctx.fillPath()
                }
                ctx.restoreGState()
                x += ww + space
            }
        }
    }
}

func renderCaptions(_ spec: [String: Any]) {
    guard let output = spec["output"] as? String else { fail("captions spec needs output") }
    let width = Int(num(spec["width"], 1920)), height = Int(num(spec["height"], 1080))
    let fps = num(spec["fps"], 24)
    let total = Int(num(spec["duration_frames"], 0))
    if width < 16 || height < 16 || width > 8192 || height > 8192 { fail("Invalid size \(width)x\(height)") }
    if fps <= 0 || fps > 240 { fail("Invalid fps \(fps)") }
    if total <= 0 { fail("duration_frames must be positive") }
    let cues: [Cue] = (spec["cues"] as? [[String: Any]] ?? []).map { c in
        let words = (c["words"] as? [[String: Any]] ?? []).map {
            Word(text: $0["text"] as? String ?? "", start: Int(num($0["start"], 0)), end: Int(num($0["end"], 0)))
        }
        return Cue(start: Int(num(c["start"], 0)), end: Int(num(c["end"], 0)), words: words)
    }.filter { $0.end > $0.start && !$0.words.isEmpty }.sorted { $0.start < $1.start }
    let renderer = CaptionRenderer(spec: spec, width: width, height: height)

    let url = URL(fileURLWithPath: output)
    try? FileManager.default.removeItem(at: url)
    let writer: AVAssetWriter
    do { writer = try AVAssetWriter(outputURL: url, fileType: .mov) } catch { fail("Could not create \(output): \(error.localizedDescription)") }
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
        AVVideoCodecKey: AVVideoCodecType.proRes4444,
        AVVideoWidthKey: width,
        AVVideoHeightKey: height,
    ])
    input.expectsMediaDataInRealTime = false
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        kCVPixelBufferWidthKey as String: width,
        kCVPixelBufferHeightKey as String: height,
        kCVPixelBufferIOSurfacePropertiesKey as String: [:],
    ])
    guard writer.canAdd(input) else { fail("ProRes 4444 is not available for writing on this Mac") }
    writer.add(input)
    guard writer.startWriting() else { fail("Could not start writing: \(writer.error?.localizedDescription ?? "unknown")") }
    writer.startSession(atSourceTime: .zero)

    // 23.976 etc.: express time in 1/1000 frame units.
    let timescale = CMTimeScale(max(1, (fps * 1000).rounded()))
    var previousKey = ""
    var previous: CVPixelBuffer?
    var cueIndex = 0
    var drawn = 0

    func newBuffer() -> CVPixelBuffer {
        var buffer: CVPixelBuffer?
        if let pool = adaptor.pixelBufferPool { CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) }
        if buffer == nil {
            CVPixelBufferCreate(nil, width, height, kCVPixelFormatType_32BGRA,
                                [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &buffer)
        }
        guard let b = buffer else { fail("Could not allocate a frame buffer") }
        return b
    }

    for frame in 0..<total {
        while cueIndex < cues.count && cues[cueIndex].end <= frame { cueIndex += 1 }
        let cue: Cue? = (cueIndex < cues.count && cues[cueIndex].start <= frame) ? cues[cueIndex] : nil
        var active = -1
        var key = "empty"
        if let cue = cue {
            for (i, w) in cue.words.enumerated() where w.start <= frame { active = i }
            let bounce = (renderer.style == "pop" && active >= 0) ? min(4, max(0, frame - cue.words[active].start)) : 0
            key = "\(cueIndex)|\(active)|\(bounce)"
        }
        let buffer: CVPixelBuffer
        if key == previousKey, let prev = previous {
            buffer = prev  // Unchanged content: reuse the last frame.
        } else {
            buffer = newBuffer()
            CVPixelBufferLockBaseAddress(buffer, [])
            let base = CVPixelBufferGetBaseAddress(buffer)
            let stride = CVPixelBufferGetBytesPerRow(buffer)
            memset(base, 0, stride * height)
            if let cue = cue, let ctx = CGContext(data: base, width: width, height: height, bitsPerComponent: 8, bytesPerRow: stride,
                                                   space: srgb, bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue) {
                ctx.setAllowsAntialiasing(true)
                ctx.setShouldSmoothFonts(false)
                renderer.draw(cue: cue, frame: frame, active: active, into: ctx)
            }
            CVPixelBufferUnlockBaseAddress(buffer, [])
            previous = buffer
            previousKey = key
            drawn += 1
        }
        while !input.isReadyForMoreMediaData { usleep(500) }
        let time = CMTime(value: CMTimeValue(frame) * 1000, timescale: timescale)
        if !adaptor.append(buffer, withPresentationTime: time) {
            fail("Writing frame \(frame) failed: \(writer.error?.localizedDescription ?? "unknown")")
        }
    }
    input.markAsFinished()
    let done = DispatchSemaphore(value: 0)
    writer.finishWriting { done.signal() }
    done.wait()
    if writer.status != .completed { fail("Could not finish \(output): \(writer.error?.localizedDescription ?? "unknown")") }
    emit(["ok": true, "output": output, "frames": total, "drawn_frames": drawn, "cues": cues.count])
}


// MARK: - index (Apple Vision: on-screen text, scene labels, faces)

/// spec {output, frames:[{path, t}], ocr: true, labels: true, faces: true, ocr_level: "accurate"|"fast",
///       label_min: 0.25, max_labels: 6}. Writes {"frames":[{t, text:[...], labels:[[id, conf]], faces:[...]}]}
/// to `output` (results can be large) and prints a short summary.
func indexFrames(_ spec: [String: Any]) {
    guard let output = spec["output"] as? String else { fail("index needs output") }
    let frames = spec["frames"] as? [[String: Any]] ?? []
    if frames.isEmpty { fail("index needs frames") }
    let doOCR = spec["ocr"] as? Bool ?? true
    let doLabels = spec["labels"] as? Bool ?? true
    let doFaces = spec["faces"] as? Bool ?? true
    let accurate = (spec["ocr_level"] as? String ?? "accurate") != "fast"
    let labelMin = Float(num(spec["label_min"], 0.25))
    let maxLabels = Int(num(spec["max_labels"], 6))
    var results = [[String: Any]?](repeating: nil, count: frames.count)
    let lock = NSLock()
    DispatchQueue.concurrentPerform(iterations: frames.count) { i in
        autoreleasepool {
            let frame = frames[i]
            let t = num(frame["t"], 0)
            var entry: [String: Any] = ["t": t]
            guard let path = frame["path"] as? String, let image = loadImage(path) else {
                entry["error"] = "unreadable"
                lock.lock(); results[i] = entry; lock.unlock()
                return
            }
            let handler = VNImageRequestHandler(cgImage: image, options: [:])
            var requests: [VNRequest] = []
            let text = VNRecognizeTextRequest()
            text.recognitionLevel = accurate ? .accurate : .fast
            text.usesLanguageCorrection = accurate
            if doOCR { requests.append(text) }
            let classify = VNClassifyImageRequest()
            if doLabels { requests.append(classify) }
            let faces = VNDetectFaceRectanglesRequest()
            if doFaces { requests.append(faces) }
            do { try handler.perform(requests) } catch { entry["error"] = error.localizedDescription }
            if doOCR {
                entry["text"] = (text.results ?? []).compactMap { obs -> String? in
                    guard let best = obs.topCandidates(1).first, best.confidence >= 0.3 else { return nil }
                    return best.string
                }
            }
            if doLabels {
                let labels = (classify.results ?? [])
                    .filter { $0.confidence >= labelMin }
                    .sorted { $0.confidence > $1.confidence }
                    .prefix(maxLabels)
                    .map { [$0.identifier, (Double($0.confidence) * 100).rounded() / 100] as [Any] }
                entry["labels"] = Array(labels)
            }
            if doFaces {
                entry["faces"] = (faces.results ?? []).map { f -> [String: Any] in
                    var face: [String: Any] = [
                        "x": (Double(f.boundingBox.origin.x) * 1000).rounded() / 1000,
                        "y": (Double(f.boundingBox.origin.y) * 1000).rounded() / 1000,
                        "w": (Double(f.boundingBox.width) * 1000).rounded() / 1000,
                        "h": (Double(f.boundingBox.height) * 1000).rounded() / 1000,
                    ]
                    // Radians; 0 yaw/pitch = facing the camera.
                    if let yaw = f.yaw?.doubleValue { face["yaw"] = (yaw * 100).rounded() / 100 }
                    if let pitch = f.pitch?.doubleValue { face["pitch"] = (pitch * 100).rounded() / 100 }
                    if let roll = f.roll?.doubleValue { face["roll"] = (roll * 100).rounded() / 100 }
                    return face
                }
            }
            lock.lock(); results[i] = entry; lock.unlock()
        }
    }
    let payload: [String: Any] = ["frames": results.map { $0 ?? [:] }]
    guard let data = try? JSONSerialization.data(withJSONObject: payload, options: []) else { fail("could not encode index") }
    do { try data.write(to: URL(fileURLWithPath: output)) } catch { fail("could not write \(output): \(error.localizedDescription)") }
    emit(["ok": true, "output": output, "frames": frames.count])
}

// MARK: - main

let args = CommandLine.arguments
guard args.count == 3 else { fail("usage: ResolveRender sheet|captions|index <spec.json>") }
let spec = loadSpec(args[2])
switch args[1] {
case "sheet": renderSheet(spec)
case "captions": renderCaptions(spec)
case "index": indexFrames(spec)
default: fail("Unknown command \(args[1]); use sheet, captions or index")
}
