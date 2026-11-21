"""A small in-memory Fusion comp for bridge tests.

Wiring uses the same Python calls the bridge makes in Resolve. When the
optional ``lupa`` package is installed, Execute() really runs the generated Lua
against this comp, so scripts are checked for syntax and for touching only
inputs that exist. Without lupa, Execute() records the script and reports it
finished. Neither replaces a check in a real Resolve.
"""

try:  # Fusion runs LuaJIT; prefer it, then any Lua lupa ships.
    import lupa.luajit21 as lupa
except ImportError:
    try:
        import lupa
    except ImportError:  # optional
        lupa = None

INPUTS = {
    "MediaIn": (),
    "MediaOut": ("Input",),
    "Transform": ("Input", "Size", "Center", "EffectMask"),
    "Blur": ("Input", "XBlurSize", "YBlurSize", "EffectMask"),
    "Defocus": ("Input", "XDefocusSize", "YDefocusSize", "EffectMask"),
    "DirectionalBlur": ("Input", "Length", "EffectMask"),
    "BrightnessContrast": ("Input", "Gain", "Saturation", "EffectMask"),
    "ColorCorrector": ("Input", "MasterSaturation", "WheelTintAngle1", "WheelTintLength1", "MasterRGBGain", "EffectMask"),
    "Merge": ("Background", "Foreground", "Blend", "Center", "EffectMask"),
    "Background": ("TopLeftRed", "TopLeftAlpha", "EffectMask"),
    "TextPlus": ("StyledText", "Size", "Center", "EffectMask"),
    "EllipseMask": ("Width", "Height", "Center", "SoftEdge", "EffectMask"),
    "RectangleMask": ("Width", "Height", "Center", "SoftEdge", "CornerRadius", "EffectMask"),
    "Glow": ("Input", "Glow", "EffectMask"),
    "TimeSpeed": ("Input", "Speed"),
    "XYPath": ("X", "Y"),
    "Loader": ("Clip", "EffectMask"),
    "PolyPath": ("Displacement",),
}
# Fusion's GetInput returns nil for image inputs (unconnected or not), unlike value inputs.
IMAGE_INPUTS = {"Input", "Background", "Foreground", "EffectMask"}
DEFAULTS = {"Size": 1.0, "Center": (0.5, 0.5), "Gain": 1.0, "Saturation": 1.0, "Width": 0.5, "Height": 0.5,
            "Blend": 1.0, "StyledText": ""}
NO_MAIN_INPUT = {"Background", "TextPlus", "EllipseMask", "RectangleMask", "XYPath"}


class Output:
    def __init__(self, tool):
        self.tool = tool

    def GetTool(self):
        return self.tool


class Modifier:
    """A BezierSpline feeding an input. Like Fusion, it hangs off the input but is not a node in the flow."""

    def __init__(self, reg_id="BezierSpline"):
        self.reg_id, self.Name = reg_id, reg_id
        self.main_output = Output(self)

    def GetAttrs(self, key=None):
        attrs = {"TOOLS_RegID": self.reg_id, "TOOLS_Name": self.Name}
        return attrs.get(key) if key else attrs


class Input:
    def __init__(self, tool, name):
        self.tool, self.id = tool, name
        self.source = None
        self.value = DEFAULTS.get(name, 0.0)
        self.keys = None
        self.path = None

    def GetConnectedOutput(self):
        return self.source

    def ConnectTo(self, target):
        self.source = target.main_output if isinstance(target, Tool) else target
        return True

    def GetAttrs(self, key=None):
        kind = ("Mask" if self.id == "EffectMask" else "Image" if self.id in IMAGE_INPUTS else
                "Point" if self.id == "Center" else "Text" if self.id in ("StyledText", "Clip") else "Number")
        attrs = {"INPS_ID": self.id, "INPS_DataType": kind}
        return attrs.get(key) if key else attrs

    def at(self, time):
        if self.path is not None:
            return (self.path.inputs["X"].at(time), self.path.inputs["Y"].at(time))
        if self.keys is None:
            return self.value
        if not self.keys:
            return None
        frames = sorted(self.keys)
        if time <= frames[0]:
            return self.keys[frames[0]]
        if time >= frames[-1]:
            return self.keys[frames[-1]]
        for a, b in zip(frames, frames[1:]):
            if a <= time <= b:
                return self.keys[a] + (self.keys[b] - self.keys[a]) * (time - a) / float(b - a)


class Tool:
    def __init__(self, comp, reg_id, name):
        self.comp, self.reg_id, self.Name = comp, reg_id, name
        self.inputs = {key: Input(self, key) for key in INPUTS[reg_id]}
        self.main_output = Output(self)
        self.data = {}
        self.deleted = False

    def GetAttrs(self, key=None):
        attrs = {"TOOLS_RegID": self.reg_id, "TOOLS_Name": self.Name}
        return attrs.get(key) if key else attrs

    def SetAttrs(self, attrs):
        name = attrs.get("TOOLS_Name")
        if name:
            if self.comp.FindTool(name) not in (None, self):
                name += "_1"
            self.Name = name
        return True

    def SetData(self, key, value):
        self.data[key] = value

    def GetData(self, key):
        return self.data.get(key)

    def FindMainInput(self, index):
        names = INPUTS[self.reg_id]
        return self.inputs[names[0]] if names and self.reg_id not in NO_MAIN_INPUT else None

    def FindMainOutput(self, index):
        return self.main_output

    def GetInputList(self):
        return {i + 1: inp for i, inp in enumerate(self.inputs.values())}

    def ConnectInput(self, name, target):
        inp = self.inputs.get(name)
        if inp is None:
            return False
        inp.keys = inp.path = None
        if target is None:
            inp.source = None
        else:
            inp.ConnectTo(target)
        return True

    def GetInput(self, name, time=None):
        inp = self.inputs.get(name)
        if inp is None or name in IMAGE_INPUTS:
            return None
        return inp.at(time if time is not None else self.comp.time)

    def SetInput(self, name, value, time=None):
        inp = self.inputs.get(name)
        if inp is None:
            return False
        if inp.keys is not None and time is not None:
            inp.keys[time] = value
        else:
            inp.value = value
        return None  # Native Fusion setters can succeed without a return value.

    def AddModifier(self, name, kind):
        inp = self.inputs.get(name)
        if inp is None:
            return False
        if kind == "BezierSpline":
            inp.keys, inp.path = {}, None
            inp.source = Modifier().main_output
        elif kind == "XYPath":
            inp.path = self.comp.AddTool(kind)
            inp.source = inp.path.main_output
        else:
            return False
        return True

    def Delete(self):
        self.deleted = True
        self.comp.tools.remove(self)
        for tool in self.comp.tools:
            for inp in tool.inputs.values():
                if inp.source is self.main_output:
                    inp.source = None


class Comp:
    def __init__(self, render_start=0, user_nodes=()):
        self.tools, self.data, self.scripts, self.locks = [], {}, [], 0
        self.render_start = render_start
        self.time = render_start
        self.counts = {}
        self.fail_lua = False
        media_in = self.AddTool("MediaIn")
        media_out = self.AddTool("MediaOut")
        previous = media_in.main_output
        for reg_id in user_nodes:
            node = self.AddTool(reg_id)
            node.FindMainInput(1).ConnectTo(previous)
            previous = node.main_output
        media_out.FindMainInput(1).ConnectTo(previous)

    def AddTool(self, reg_id, x=None, y=None):
        if reg_id not in INPUTS:
            return None
        self.counts[reg_id] = self.counts.get(reg_id, 0) + 1
        tool = Tool(self, reg_id, "%s%d" % (reg_id, self.counts[reg_id]))
        self.tools.append(tool)
        return tool

    def FindTool(self, name):
        return next((tool for tool in self.tools if tool.Name == name), None)

    def GetToolList(self, selected=False):
        return {i + 1: tool for i, tool in enumerate(self.tools)}

    def GetAttrs(self, key=None):
        attrs = {"COMPN_RenderStart": float(self.render_start)}
        return attrs.get(key) if key else attrs

    def SetData(self, key, value):
        self.data[key] = value

    def GetData(self, key):
        return self.data.get(key)

    def Lock(self):
        self.locks += 1

    def Unlock(self):
        self.locks -= 1

    def StartUndo(self, name):
        self.undo_depth = getattr(self, "undo_depth", 0) + 1

    def EndUndo(self, keep):
        self.undo_depth -= 1
        self.undo_groups = getattr(self, "undo_groups", 0) + (1 if keep else 0)

    def Execute(self, script):
        self.scripts.append(script)
        if self.fail_lua:
            return None
        if lupa is None:
            marker = script.rsplit("comp:SetData(", 1)[-1]
            key, _, value = marker.partition(", ")
            self.SetData(key.strip('"'), value.rstrip(")").strip('"'))
            return None
        runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
        runtime.globals().SIM = Sim(self)
        try:
            runtime.execute(LUA_PRELUDE + "\n" + script)
        except lupa.LuaError as exc:  # Fusion prints Lua errors and returns.
            self.last_error = str(exc)
        return None

    def chain(self):
        """Tool names from MediaOut upstream along main inputs."""
        names, output = [], self.FindTool("MediaOut1").FindMainInput(1).GetConnectedOutput()
        while output is not None:
            names.append(output.tool.Name)
            main = output.tool.FindMainInput(1)
            output = main.GetConnectedOutput() if main is not None else None
        return names


class Sim:
    """Python side of the Lua proxies: every call fails like Fusion on a missing input."""

    def __init__(self, comp):
        self.comp = comp

    def input(self, tool, name):
        inp = tool.inputs.get(name)
        if inp is None:
            raise AttributeError("%s has no input %s" % (tool.Name, name))
        return inp

    def find(self, name):
        return self.comp.FindTool(name)

    def set_data(self, key, value):
        self.comp.SetData(key, value)

    def get_data(self, key):
        return self.comp.GetData(key)

    def connect(self, tool, name, target):
        self.input(tool, name)
        tool.ConnectInput(name, target)

    def animate(self, tool, name):
        inp = self.input(tool, name)
        inp.keys, inp.path, inp.source = {}, None, Modifier().main_output

    def xypath(self, tool, name):
        inp = self.input(tool, name)
        inp.path = self.comp.AddTool("XYPath")
        inp.keys, inp.source = None, inp.path.main_output

    def path_tool(self, tool, name):
        return self.input(tool, name).path

    def key(self, tool, name, time, value):
        inp = self.input(tool, name)
        if inp.keys is None:
            raise ValueError("%s.%s is not animated" % (tool.Name, name))
        inp.keys[float(time)] = float(value)

    def set_value(self, tool, name, value):
        self.input(tool, name).value = value if isinstance(value, str) else float(value)

    def point(self, tool, name, x, y):
        self.input(tool, name).value = (float(x), float(y))


LUA_PRELUDE = r"""
local sim = SIM
SIM = nil
local BEZIER, XYPATH, raw = {}, {}, setmetatable({}, {__mode = "k"})
function BezierSpline() return BEZIER end
function XYPath() return XYPATH end
local wrap
local function input_proxy(tool, name)
  return setmetatable({}, {
    __newindex = function(_, time, value) sim.key(tool, name, time, value) end,
    __index = function(_, key)
      if key == "GetConnectedOutput" then
        return function() return {GetTool = function() return wrap(sim.path_tool(tool, name)) end} end
      end
    end,
  })
end
wrap = function(tool)
  if tool == nil then return nil end
  local proxy = setmetatable({}, {
    __index = function(_, key)
      if key == "ConnectInput" then
        return function(_, name, target) sim.connect(tool, name, target and raw[target]) end
      end
      return input_proxy(tool, key)
    end,
    __newindex = function(_, key, value)
      if value == BEZIER then sim.animate(tool, key)
      elseif value == XYPATH then sim.xypath(tool, key)
      elseif type(value) == "table" then sim.point(tool, key, value[1], value[2])
      else sim.set_value(tool, key, value) end
    end,
  })
  raw[proxy] = tool
  return proxy
end
comp = {
  FindTool = function(_, name) return wrap(sim.find(name)) end,
  SetData = function(_, key, value) sim.set_data(key, value) end,
  GetData = function(_, key) return sim.get_data(key) end,
}
"""


def comp_text(comp, media_path="/media/clip.mov"):
    """A small .comp text in ExportFusionComp's shape (tools only, no wiring).

    Like Resolve's export, the clip's MediaIn is written as a Loader carrying MediaProps; a real file
    Loader keeps its Clip Filename.
    """
    lines = ["Composition {", "\tTools = ordered() {"]
    for tool in comp.tools:
        if tool.reg_id == "MediaIn":
            lines.append('\t\t%s = Loader { CustomData = { MediaProps = { MEDIA_PATH = "%s" } }, '
                         'Clips = { Clip { Filename = "%s" } } },' % (tool.Name, media_path, media_path))
        elif tool.reg_id == "Loader":
            lines.append('\t\t%s = Loader { Clips = { Clip { ID = "Clip1", Filename = "%s" } } },'
                         % (tool.Name, tool.inputs["Clip"].value))
        else:
            lines.append("\t\t%s = %s { },"% (tool.Name, tool.reg_id))
    return "\n".join(lines + ["\t}", "}"]) + "\n"


def comp_from_text(text, convert_loaders=False):
    """Rebuild a Comp from comp_text output. ``convert_loaders`` turns file Loaders into MediaIns, as Resolve
    21.2 Free did on ImportFusionComp (GetInput("Clip") then read nil)."""
    import re
    comp = Comp()
    for name, reg_id, body in re.findall(r"^\s*(\w+) = (\w+) \{(.*)\},?$", text, re.M):
        if comp.FindTool(name) is not None:
            continue
        if reg_id == "Loader":
            reg_id = "MediaIn" if "MediaProps" in body or convert_loaders else "Loader"
        tool = comp.AddTool(reg_id)
        if tool is None:
            continue
        tool.Name = name
        found = re.search(r'Filename = "([^"]*)"', body)
        if reg_id == "Loader" and found:
            tool.inputs["Clip"].value = found.group(1)
    return comp
