// Resolve AI Chat: a native macOS panel that lives with DaVinci Resolve.
//
// - Starts the local chat server (chat/server.py) and shows it in a WKWebView.
// - The panel (or its collapsed bubble) is visible only while Resolve or this app is in front.
// - Placement is stored relative to Resolve's main window, so the panel follows Resolve when it
//   moves or resizes, while the user can drag and resize the panel freely.
// - ⌥⌘A toggles panel ↔ bubble; resolveaichat://open expands the panel.
//
// JS → native (window.webkit.messageHandlers.shell.postMessage):
//   {type:"ready"}                      page loaded; native replies with a `shell` event
//   {type:"drag"}                       start a window drag (header mousedown)
//   {type:"collapse"} / {type:"hide"}   collapse the panel into the bubble
//   {type:"expand"}                     show the panel
//   {type:"badge", state:"idle"|"running"|"approval"}   bubble badge (a run that ends while collapsed shows "done")
//   {type:"toggle-dock"} / {type:"toggle-follow"}       toggle following Resolve
//   {type:"reset-position"}             restore the default placement
// native → JS: window.dispatchEvent(new CustomEvent('shell', {detail:{follows, collapsed, docked}}))
//
// Build: chat/macos/build.sh

import AppKit
import Carbon.HIToolbox
import WebKit

let resolveOwners: Set<String> = ["DaVinci Resolve", "Resolve"]
let resolveBundleIDs: Set<String> = [
    "com.blackmagic-design.DaVinciResolve", "com.blackmagic-design.DaVinciResolveLite",
    "com.blackmagic-design.DaVinciResolveApp", "com.blackmagic-design.DaVinciResolveStudio",
]

struct Config: Decodable {
    let python: String
    let server: String
    let home: String?
}

/// A placement relative to Resolve's window: insets from its top-right corner plus a size.
struct Placement: Codable, Equatable {
    var right: CGFloat
    var top: CGFloat
    var width: CGFloat
    var height: CGFloat

    func frame(in resolve: NSRect) -> NSRect {
        NSRect(x: resolve.maxX - right - width, y: resolve.maxY - top - height, width: width, height: height)
    }

    static func from(_ frame: NSRect, in resolve: NSRect) -> Placement {
        Placement(right: resolve.maxX - frame.maxX, top: resolve.maxY - frame.maxY,
                  width: frame.width, height: frame.height)
    }
}

enum Store {
    static let d = UserDefaults.standard

    static var follows: Bool {
        get { d.object(forKey: "follows") as? Bool ?? (d.object(forKey: "docked") as? Bool ?? true) }
        set { d.set(newValue, forKey: "follows") }
    }
    static var collapsed: Bool {
        get { d.object(forKey: "collapsed") as? Bool ?? false }
        set { d.set(newValue, forKey: "collapsed") }
    }

    static func placement(_ key: String) -> Placement? {
        guard let data = d.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(Placement.self, from: data)
    }
    static func setPlacement(_ value: Placement?, _ key: String) {
        if let value, let data = try? JSONEncoder().encode(value) { d.set(data, forKey: key) } else { d.removeObject(forKey: key) }
    }
    static func frame(_ key: String) -> NSRect? { d.string(forKey: key).map { NSRectFromString($0) } }
    static func setFrame(_ value: NSRect, _ key: String) { d.set(NSStringFromRect(value), forKey: key) }
}

// MARK: - Windows

/// The chat panel. Non-activating, so typing in it never takes focus away from Resolve's app.
final class ChatPanel: NSPanel {
    var onCollapse: (() -> Void)?

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    // A non-activating panel's app is not active, so the main menu never sees these key
    // equivalents. Route the standard editing commands straight down the responder chain.
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        guard flags.contains(.command), let key = event.charactersIgnoringModifiers?.lowercased() else {
            return super.performKeyEquivalent(with: event)
        }
        let shift = flags.contains(.shift)
        let action: Selector?
        switch (key, shift) {
        case ("a", false): action = #selector(NSText.selectAll(_:))
        case ("c", false): action = #selector(NSText.copy(_:))
        case ("v", false): action = #selector(NSText.paste(_:))
        case ("x", false): action = #selector(NSText.cut(_:))
        case ("z", false): action = Selector(("undo:"))
        case ("z", true): action = Selector(("redo:"))
        case ("w", false):
            onCollapse?()
            return true
        default: action = nil
        }
        if let action, NSApp.sendAction(action, to: nil, from: self) { return true }
        return super.performKeyEquivalent(with: event)
    }
}

/// The collapsed bubble: a draggable circle that expands the panel on click.
final class BubblePanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class BubbleView: NSView {
    var onClick: (() -> Void)?
    var onMoved: (() -> Void)?
    /// idle | running | approval | done. Only approval and done (something waits on you) show the dot; running
    /// shows a slow moonlight arc circling the edge instead.
    var badge = "idle" { didSet { if badge != oldValue { needsDisplay = true; updateSpinner() } } }
    private let spinner = CAShapeLayer()
    private var downPoint: NSPoint?
    private var downOrigin: NSPoint?
    private var dragging = false
    private var hovering = false { didSet { needsDisplay = true } }

    override var isFlipped: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        let circle = bounds.insetBy(dx: 4, dy: 4)
        spinner.frame = bounds
        // A quarter arc on the rim, fading from clear at its tail to soft moonlight at its head.
        let arc = CGMutablePath()
        arc.addEllipse(in: circle)
        spinner.path = arc
        spinner.fillColor = nil
        spinner.lineWidth = 1.6
        spinner.lineCap = .round
        spinner.strokeColor = NSColor(srgbRed: 0xc4 / 255, green: 0xd2 / 255, blue: 0xe6 / 255, alpha: 0.75).cgColor
        spinner.strokeStart = 0
        spinner.strokeEnd = 0.24
        spinner.isHidden = true
        layer?.addSublayer(spinner)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    private func updateSpinner() {
        let running = badge == "running"
        spinner.isHidden = !running
        spinner.removeAllAnimations()
        guard running else { return }
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            spinner.opacity = 0.5  // Still, but still there.
            return
        }
        spinner.opacity = 1
        // Rotate the arc around the circle's centre: slow, linear, endless. Nothing pulses or flashes.
        spinner.anchorPoint = CGPoint(x: 0.5, y: 0.5)
        spinner.position = CGPoint(x: bounds.midX, y: bounds.midY)
        let spin = CABasicAnimation(keyPath: "transform.rotation.z")
        spin.fromValue = 0
        spin.toValue = -2 * Double.pi
        spin.duration = 2.6
        spin.repeatCount = .infinity
        spin.isRemovedOnCompletion = false
        spinner.add(spin, forKey: "spin")
        // And a gentle breath in the arc's length so it reads as alive, not mechanical.
        let breathe = CABasicAnimation(keyPath: "strokeEnd")
        breathe.fromValue = 0.16
        breathe.toValue = 0.3
        breathe.duration = 1.9
        breathe.autoreverses = true
        breathe.repeatCount = .infinity
        breathe.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        breathe.isRemovedOnCompletion = false
        spinner.add(breathe, forKey: "breathe")
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }
    override func mouseEntered(with event: NSEvent) { hovering = true }
    override func mouseExited(with event: NSEvent) { hovering = false }

    override func draw(_ dirtyRect: NSRect) {
        let circle = bounds.insetBy(dx: 4, dy: 4)
        let path = NSBezierPath(ovalIn: circle)
        NSColor(srgbRed: 0x1d / 255, green: 0x1d / 255, blue: 0x1d / 255, alpha: 1).setFill()
        path.fill()
        (hovering ? NSColor(srgbRed: 0x4a / 255, green: 0x4a / 255, blue: 0x4a / 255, alpha: 1)
                  : NSColor(srgbRed: 0x36 / 255, green: 0x36 / 255, blue: 0x36 / 255, alpha: 1)).setStroke()
        path.lineWidth = 1
        path.stroke()

        let ink = NSColor(srgbRed: 0xf0 / 255, green: 0xf0 / 255, blue: 0xf0 / 255, alpha: 1)
        let config = NSImage.SymbolConfiguration(pointSize: 17, weight: .medium)
            .applying(NSImage.SymbolConfiguration(paletteColors: [ink]))
        if let glyph = NSImage(systemSymbolName: "bubble.left.and.text.bubble.right.fill",
                               accessibilityDescription: "Resolve AI Chat")?.withSymbolConfiguration(config) {
            let size = glyph.size
            glyph.draw(in: NSRect(x: circle.midX - size.width / 2, y: circle.midY - size.height / 2,
                                  width: size.width, height: size.height))
        }

        if badge == "approval" || badge == "done" {  // Waiting on you: a reply to read or an edit to allow.
            let dot = NSRect(x: circle.maxX - 11, y: circle.maxY - 11, width: 10, height: 10)
            let ring = NSBezierPath(ovalIn: dot.insetBy(dx: -2, dy: -2))
            NSColor(srgbRed: 0x17 / 255, green: 0x17 / 255, blue: 0x17 / 255, alpha: 1).setFill()
            ring.fill()
            // The panel's one accent: the warm window light.
            NSColor(srgbRed: 0xea / 255, green: 0xd9 / 255, blue: 0xae / 255, alpha: 1).setFill()
            NSBezierPath(ovalIn: dot).fill()
        }
        window?.invalidateShadow()
    }

    override func mouseDown(with event: NSEvent) {
        downPoint = NSEvent.mouseLocation
        downOrigin = window?.frame.origin
        dragging = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard let start = downPoint, let origin = downOrigin, let window else { return }
        let now = NSEvent.mouseLocation
        let dx = now.x - start.x, dy = now.y - start.y
        if !dragging && hypot(dx, dy) < 3 { return }  // Below the threshold it is still a click.
        dragging = true
        window.setFrameOrigin(NSPoint(x: origin.x + dx, y: origin.y + dy))
    }

    override func mouseUp(with event: NSEvent) {
        defer { downPoint = nil; downOrigin = nil; dragging = false }
        if dragging { onMoved?() } else { onClick?() }
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate,
                         WKUIDelegate, NSWindowDelegate {
    static let bubbleSize: CGFloat = 56  // 48pt circle plus room for the hover ring.

    var panel: ChatPanel!
    var bubble: BubblePanel!
    var bubbleView: BubbleView!
    var web: WKWebView!
    var server: Process?
    var serverURL: URL?
    var statusItem: NSStatusItem!
    var timer: Timer?
    var hotKey: EventHotKeyRef?

    var applyingFrame = false      // We are moving a window ourselves; ignore move/resize callbacks.
    var userResizing = false       // Live resize in progress: never fight the user.
    var userDragging = false       // performDrag in progress: never fight the user.
    var lastResolveFrame: NSRect?  // Resolve's frame at our last placement. Nil forces a placement.
    var pendingResolve: NSRect?    // A changed Resolve frame seen once; we move only if a later read agrees.
    var pendingSince = Date.distantPast
    var visible = false            // Whether we currently show something.
    var pickingFiles = false       // An open panel is up; keep showing.

    // ------------------------------------------------------------ lifecycle

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildMainMenu()
        buildPanel()
        buildBubble()
        buildStatusItem()
        registerHotKey()
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleURL(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
        let center = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.didActivateApplicationNotification, NSWorkspace.didHideApplicationNotification,
                     NSWorkspace.didUnhideApplicationNotification, NSWorkspace.activeSpaceDidChangeNotification,
                     NSWorkspace.didTerminateApplicationNotification] {
            center.addObserver(self, selector: #selector(workspaceChanged(_:)), name: name, object: nil)
        }
        startServer()
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.tick() }
        tick()
    }

    func applicationWillTerminate(_ note: Notification) {
        server?.terminate()
    }

    // ------------------------------------------------------------ main menu

    /// A standard Edit menu, used when this app is active (e.g. after choosing a menu bar item).
    func buildMainMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "Quit Resolve AI Chat", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        let redo = NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(redo)
        edit.addItem(.separator())
        edit.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        edit.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        edit.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        edit.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    // ---------------------------------------------------------------- panel

    func buildPanel() {
        let frame = Store.frame("panelFrame") ?? NSRect(x: 200, y: 200, width: 400, height: 760)
        panel = ChatPanel(contentRect: frame,
                          styleMask: [.titled, .resizable, .fullSizeContentView, .nonactivatingPanel],
                          backing: .buffered, defer: false)
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isMovableByWindowBackground = false
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = false
        // Resolve stays the active app while the pointer is over us; without this the web view never
        // hears mouse moves, so :hover styles (message controls, row highlights) never show.
        panel.acceptsMouseMovedEvents = true
        panel.collectionBehavior = [.fullScreenAuxiliary, .moveToActiveSpace]
        panel.backgroundColor = NSColor(srgbRed: 0.09, green: 0.09, blue: 0.09, alpha: 1)
        panel.appearance = NSAppearance(named: .darkAqua)
        panel.minSize = NSSize(width: 320, height: 420)
        panel.delegate = self
        panel.onCollapse = { [weak self] in self?.collapse() }
        for button in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] {
            panel.standardWindowButton(button)?.isHidden = true
        }

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "shell")
        config.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: panel.contentView!.bounds, configuration: config)
        web.autoresizingMask = [.width, .height]
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
        web.uiDelegate = self
        panel.contentView?.addSubview(web)
        web.loadHTMLString(placeholder("Starting Resolve AI Chat…"), baseURL: nil)
    }

    func buildBubble() {
        let size = AppDelegate.bubbleSize
        bubble = BubblePanel(contentRect: NSRect(x: 0, y: 0, width: size, height: size),
                             styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        bubble.isOpaque = false
        bubble.backgroundColor = .clear
        bubble.hasShadow = true
        bubble.level = .floating
        bubble.hidesOnDeactivate = false
        bubble.isFloatingPanel = true
        bubble.collectionBehavior = [.fullScreenAuxiliary, .moveToActiveSpace]
        bubbleView = BubbleView(frame: NSRect(x: 0, y: 0, width: size, height: size))
        bubbleView.onClick = { [weak self] in self?.expand() }
        bubbleView.onMoved = { [weak self] in self?.saveBubblePlacement() }
        bubble.contentView = bubbleView
        bubble.setAccessibilityLabel("Resolve AI Chat")
    }

    func placeholder(_ text: String) -> String {
        "<html><body style='margin:0;height:100vh;display:grid;place-items:center;background:#171717;color:#a5a5a5;font:12.5px -apple-system;padding:24px;text-align:center'>\(text)</body></html>"
    }

    // --------------------------------------------------------------- server

    func loadConfig() -> Config? {
        let candidates = [
            Bundle.main.url(forResource: "config", withExtension: "json"),
            FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(
                "Library/Containers/com.blackmagic-design.DaVinciResolveLite/Data/.resolve-ai-bridge/chat-app.json"),
            FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".resolve-ai-bridge/chat-app.json"),
        ].compactMap { $0 }
        for url in candidates {
            if let data = try? Data(contentsOf: url), let cfg = try? JSONDecoder().decode(Config.self, from: data) {
                return cfg
            }
        }
        return nil
    }

    func startServer() {
        guard let cfg = loadConfig() else {
            web.loadHTMLString(placeholder("Resolve AI Chat is not configured. Run <code>python3 install.py</code> in the bridge repository."), baseURL: nil)
            return
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: cfg.python)
        proc.arguments = [cfg.server]
        var env = ProcessInfo.processInfo.environment
        if let home = cfg.home { env["RESOLVE_AI_BRIDGE_HOME"] = home }
        // GUI apps get a minimal PATH; agents need the user's tools (claude, codex, node, ffmpeg).
        let extra = ["\(NSHomeDirectory())/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "\(NSHomeDirectory())/.npm-global/bin"]
        env["PATH"] = (extra + [(env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")]).joined(separator: ":")
        proc.environment = env
        let out = Pipe()
        proc.standardOutput = out
        proc.standardError = FileHandle.nullDevice
        var buffer = Data()
        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            if chunk.isEmpty { handle.readabilityHandler = nil; return }
            buffer.append(chunk)
            guard let newline = buffer.firstIndex(of: 0x0A) else { return }
            let line = buffer[..<newline]
            handle.readabilityHandler = nil
            if let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
               let s = obj["url"] as? String, let url = URL(string: s) {
                DispatchQueue.main.async { self?.serverURL = url; self?.web.load(URLRequest(url: url)) }
            }
        }
        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                // A reused server answers and exits 0; keep the page. Otherwise report it.
                if p.terminationStatus != 0 {
                    self?.web.loadHTMLString(self?.placeholder("The chat server stopped (code \(p.terminationStatus)). Choose Restart Server from the menu bar icon.") ?? "", baseURL: nil)
                }
            }
        }
        do {
            try proc.run()
            server = proc
        } catch {
            web.loadHTMLString(placeholder("Could not start the chat server: \(error.localizedDescription)"), baseURL: nil)
        }
    }

    @objc func restartServer() {
        server?.terminate()
        server = nil
        web.loadHTMLString(placeholder("Restarting…"), baseURL: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.startServer() }
    }

    // Keep the webview on our server; open everything else in the browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if url.scheme == "about" || (url.host == "127.0.0.1" && url.port == serverURL?.port) {
            return decisionHandler(.allow)
        }
        // Dropped files the page did not handle would navigate to file:// here; cancelling keeps the chat.
        if action.navigationType == .linkActivated, ["http", "https"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    // `<input type=file>` support: attachments come from a standard open panel.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let open = NSOpenPanel()
        open.allowsMultipleSelection = parameters.allowsMultipleSelection
        open.canChooseFiles = true
        open.canChooseDirectories = false
        open.prompt = "Attach"
        pickingFiles = true
        activateSelf()
        open.begin { [weak self] response in
            completionHandler(response == .OK ? open.urls : nil)
            self?.pickingFiles = false
            self?.activateResolve()
            self?.panel.makeKey()
        }
    }

    func activateSelf() {
        if #available(macOS 14.0, *) { NSApp.activate() } else { NSApp.activate(ignoringOtherApps: true) }
    }

    func activateResolve() {
        let app = NSWorkspace.shared.runningApplications.first { resolveBundleIDs.contains($0.bundleIdentifier ?? "") }
        if #available(macOS 14.0, *) { app?.activate() } else { app?.activate(options: []) }
    }

    // ------------------------------------------------------- JS ⇄ native

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "drag":
            if let event = NSApp.currentEvent {
                userDragging = true
                panel.performDrag(with: event)
            }
        case "collapse", "hide":
            collapse()
        case "expand":
            expand()
        case "badge":
            let state = (body["state"] as? String) ?? "idle"
            if state == "idle" && Store.collapsed && (bubbleView.badge == "running" || bubbleView.badge == "approval"
                                                      || bubbleView.badge == "done") {
                bubbleView.badge = "done"  // Finished while you were away: keep the dot until you open the panel.
            } else {
                bubbleView.badge = state
            }
        case "toggle-dock", "toggle-follow":
            toggleFollow()
        case "reset-position":
            resetPosition()
        case "ready":
            pushState()
        default:
            break
        }
    }

    func pushState() {
        let follows = Store.follows, collapsed = Store.collapsed
        let js = "window.dispatchEvent(new CustomEvent('shell',{detail:{follows:\(follows),collapsed:\(collapsed),docked:\(follows)}}))"
        web.evaluateJavaScript(js, completionHandler: nil)
    }

    // ------------------------------------------------------- Resolve

    func resolveApp() -> NSRunningApplication? {
        NSWorkspace.shared.runningApplications.first {
            resolveBundleIDs.contains($0.bundleIdentifier ?? "") || resolveOwners.contains($0.localizedName ?? "")
        }
    }

    /// Resolve's largest on-screen window in AppKit coordinates. Nil when Resolve is hidden,
    /// minimized, quit or on another Space (on-screen-only listing).
    func resolveWindowFrame() -> NSRect? {
        let pid = resolveApp()?.processIdentifier
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] else { return nil }
        var best: CGRect?
        for info in list {
            let owner = info[kCGWindowOwnerName as String] as? String ?? ""
            let ownerPID = info[kCGWindowOwnerPID as String] as? pid_t
            guard resolveOwners.contains(owner) || (pid != nil && ownerPID == pid),
                  (info[kCGWindowLayer as String] as? Int) == 0,
                  let dict = info[kCGWindowBounds as String] as? NSDictionary,
                  let rect = CGRect(dictionaryRepresentation: dict), rect.width > 400, rect.height > 300 else { continue }
            if best == nil || rect.width * rect.height > best!.width * best!.height { best = rect }
        }
        guard let cg = best, let primary = NSScreen.screens.first else { return nil }
        // CoreGraphics is top-left based; AppKit is bottom-left based on the primary screen.
        return NSRect(x: cg.minX, y: primary.frame.maxY - cg.maxY, width: cg.width, height: cg.height)
    }

    func resolveIsFrontmost() -> Bool {
        guard let app = NSWorkspace.shared.frontmostApplication else { return false }
        return resolveBundleIDs.contains(app.bundleIdentifier ?? "") || resolveOwners.contains(app.localizedName ?? "")
    }

    func selfIsFrontmost() -> Bool {
        NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier
    }

    // ------------------------------------------------------- placement

    func defaultPanelPlacement(in resolve: NSRect) -> Placement {
        let top: CGFloat = 30, bottom: CGFloat = 46
        return Placement(right: 0, top: top, width: min(400, resolve.width * 0.5),
                         height: max(resolve.height - top - bottom, 420))
    }

    func defaultBubblePlacement(in resolve: NSRect) -> Placement {
        let size = AppDelegate.bubbleSize
        return Placement(right: 20, top: max(resolve.height - size - 64, 40), width: size, height: size)
    }

    /// Keep a frame on the screen that holds Resolve (or the frame itself).
    func clamped(_ frame: NSRect, near anchor: NSRect) -> NSRect {
        let center = NSPoint(x: anchor.midX, y: anchor.midY)
        guard let screen = NSScreen.screens.first(where: { $0.frame.contains(center) }) ?? NSScreen.main else { return frame }
        let area = screen.visibleFrame
        var f = frame
        f.size.width = min(f.width, area.width)
        f.size.height = min(f.height, area.height)
        f.origin.x = min(max(f.minX, area.minX), area.maxX - f.width)
        f.origin.y = min(max(f.minY, area.minY), area.maxY - f.height)
        return f
    }

    static func near(_ a: NSRect, _ b: NSRect, tolerance: CGFloat = 2) -> Bool {
        abs(a.minX - b.minX) <= tolerance && abs(a.minY - b.minY) <= tolerance &&
            abs(a.width - b.width) <= tolerance && abs(a.height - b.height) <= tolerance
    }

    func setFrame(_ window: NSWindow, _ frame: NSRect) {
        guard window.frame != frame else { return }
        applyingFrame = true
        window.setFrame(frame, display: true, animate: false)
        applyingFrame = false
    }

    func targetFrame(for window: NSWindow, resolve: NSRect?) -> NSRect? {
        let isPanel = window === panel
        if Store.follows, let resolve {
            let key = isPanel ? "panelPlacement" : "bubblePlacement"
            let placement = Store.placement(key) ?? (isPanel ? defaultPanelPlacement(in: resolve) : defaultBubblePlacement(in: resolve))
            return clamped(placement.frame(in: resolve), near: resolve)
        }
        return Store.frame(isPanel ? "panelFrame" : "bubbleFrame")
    }

    func savePlacement(of window: NSWindow) {
        guard !applyingFrame else { return }
        let isPanel = window === panel
        Store.setFrame(window.frame, isPanel ? "panelFrame" : "bubbleFrame")
        if Store.follows, let resolve = resolveWindowFrame() {
            Store.setPlacement(Placement.from(window.frame, in: resolve), isPanel ? "panelPlacement" : "bubblePlacement")
            lastResolveFrame = resolve
        }
    }

    func saveBubblePlacement() { savePlacement(of: bubble) }

    // User-driven moves and resizes become the new placement; the timer never overrides them.
    func windowWillStartLiveResize(_ note: Notification) { userResizing = true }
    func windowDidEndLiveResize(_ note: Notification) {
        userResizing = false
        savePlacement(of: panel)
    }
    func windowDidMove(_ note: Notification) { if !applyingFrame { savePlacement(of: panel) } }
    func windowDidResize(_ note: Notification) { if !applyingFrame && !userResizing { savePlacement(of: panel) } }

    // ------------------------------------------------------- visibility + following

    @objc func workspaceChanged(_ note: Notification) { tick() }

    func tick() {
        if userDragging && NSEvent.pressedMouseButtons & 1 == 0 {
            userDragging = false
            savePlacement(of: panel)
        }
        let resolve = resolveWindowFrame()
        // Typing in the non-activating panel keeps Resolve frontmost, so this is enough.
        let front = resolveIsFrontmost() || selfIsFrontmost() || pickingFiles
        let show = front && (resolve != nil || pickingFiles)
        let active: NSWindow = Store.collapsed ? bubble : panel
        let idle: NSWindow = Store.collapsed ? panel : bubble

        if !show {
            if visible { panel.orderOut(nil); bubble.orderOut(nil); visible = false }
            return
        }
        // Reappearing keeps the window exactly where it was (hidden windows keep their frame). Right when
        // Resolve activates, the window list can report a transitional frame, so a new Resolve frame only
        // counts once two reads in a row agree; otherwise the panel would flash elsewhere and then jump back.
        var place = false
        if let last = lastResolveFrame {
            if let r = resolve, !Self.near(r, last) {
                if let pending = pendingResolve, Self.near(pending, r) {
                    place = Date().timeIntervalSince(pendingSince) >= 0.3  // Settled, not two reads in one burst.
                } else {
                    pendingResolve = r
                    pendingSince = Date()
                }
            } else {
                pendingResolve = nil
            }
        } else {
            place = resolve != nil || !Store.follows  // First show, collapse/expand or reset.
        }
        if place && !userResizing && !userDragging, let frame = targetFrame(for: active, resolve: resolve) {
            setFrame(active, frame)
            lastResolveFrame = resolve
            pendingResolve = nil
        }
        if idle.isVisible { idle.orderOut(nil) }
        if !active.isVisible { active.orderFrontRegardless() }
        visible = true
    }

    func collapse() {
        Store.collapsed = true
        lastResolveFrame = nil
        panel.orderOut(nil)
        visible = false
        tick()
        rebuildMenu()
        pushState()
    }

    func expand() {
        Store.collapsed = false
        if bubbleView.badge == "done" { bubbleView.badge = "idle" }  // Seen.
        lastResolveFrame = nil
        bubble.orderOut(nil)
        visible = false
        tick()
        if panel.isVisible {
            panel.makeKey()
            web.evaluateJavaScript("document.querySelector('textarea')?.focus()", completionHandler: nil)
        }
        rebuildMenu()
        pushState()
    }

    @objc func toggleCollapsed() { Store.collapsed ? expand() : collapse() }
    @objc func showPanel() { expand() }
    @objc func collapseToBubble() { collapse() }

    @objc func toggleFollow() {
        Store.follows.toggle()
        // Keep the windows where they are; just switch how their position is remembered.
        savePlacement(of: panel)
        savePlacement(of: bubble)
        lastResolveFrame = nil
        rebuildMenu()
        pushState()
    }

    @objc func resetPosition() {
        for key in ["panelPlacement", "bubblePlacement"] { Store.setPlacement(nil, key) }
        UserDefaults.standard.removeObject(forKey: "panelFrame")
        UserDefaults.standard.removeObject(forKey: "bubbleFrame")
        if !Store.follows { Store.follows = true }
        lastResolveFrame = nil
        visible = false
        tick()
        rebuildMenu()
        pushState()
    }

    @objc func handleURL(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        expand()
    }

    // ------------------------------------------------------- menu + hotkey

    func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "bubble.left.and.text.bubble.right", accessibilityDescription: "Resolve AI Chat")
        }
        rebuildMenu()
    }

    func rebuildMenu() {
        let menu = NSMenu()
        if Store.collapsed {
            let show = NSMenuItem(title: "Show Panel", action: #selector(showPanel), keyEquivalent: "a")
            show.keyEquivalentModifierMask = [.command, .option]
            menu.addItem(show)
        } else {
            let collapse = NSMenuItem(title: "Collapse to Bubble", action: #selector(collapseToBubble), keyEquivalent: "a")
            collapse.keyEquivalentModifierMask = [.command, .option]
            menu.addItem(collapse)
        }
        let follow = NSMenuItem(title: "Follow Resolve", action: #selector(toggleFollow), keyEquivalent: "")
        follow.state = Store.follows ? .on : .off
        menu.addItem(follow)
        menu.addItem(NSMenuItem(title: "Reset Position", action: #selector(resetPosition), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Open in Browser", action: #selector(openInBrowser), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Restart Server", action: #selector(restartServer), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Resolve AI Chat", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        for item in menu.items where item.action != #selector(NSApplication.terminate(_:)) { item.target = self }
        statusItem.menu = menu
    }

    @objc func openInBrowser() {
        if let url = serverURL { NSWorkspace.shared.open(url) }
    }

    func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            let me = Unmanaged<AppDelegate>.fromOpaque(userData!).takeUnretainedValue()
            DispatchQueue.main.async { me.toggleCollapsed() }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), nil)
        let id = EventHotKeyID(signature: OSType(0x52414943), id: 1)  // 'RAIC'
        RegisterEventHotKey(UInt32(kVK_ANSI_A), UInt32(cmdKey | optionKey), id, GetApplicationEventTarget(), 0, &hotKey)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
