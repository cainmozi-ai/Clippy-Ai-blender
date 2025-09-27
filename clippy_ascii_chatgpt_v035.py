# -*- coding: utf-8 -*-
bl_info = {
    "name": "Cain Clippy ASCII ChatGPT Build (SAFE, Project/Org Headers)",
    "author": "Cain Moriarty + ChatGPT",
    "version": (0, 3, 5),
    "blender": (3, 0, 0),
    "location": "3D Viewport > N panel > Clippy",
    "description": "Clippy-style overlay + chat panel using OpenAI Chat Completions with optional Organization/Project headers (ASCII only).",
    "category": "3D View",
}

import bpy
import json
import time
import math
import urllib.request
import urllib.error

from bpy.types import AddonPreferences, Panel, Operator
from bpy.props import StringProperty, BoolProperty, IntProperty, FloatProperty

ADDON_ID = __name__
LOG_TEXT_NAME = "CLIPPY_CHAT_LOG"

# ----------------------- preferences ----------------------------------------

def get_prefs(context=None):
    if context is None:
        context = bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None

class CLIPPY_Prefs(AddonPreferences):
    bl_idname = ADDON_ID

    # Chat settings
    api_key: StringProperty(name="OpenAI API Key", subtype='PASSWORD', default="")
    api_base: StringProperty(name="API Base URL", default="https://api.openai.com/v1")
    model: StringProperty(name="Model", default="gpt-4o-mini")
    system_prompt: StringProperty(
        name="System Prompt",
        default="You are Clippy, a concise Blender assistant. Keep replies short and specific to Blender."
    )
    temperature: FloatProperty(name="Temperature", default=0.2, min=0.0, max=2.0)
    max_tokens: IntProperty(name="Max Tokens", default=400, min=64, max=4096)
    context_messages: IntProperty(name="Context Messages", default=8, min=2, max=24)

    # Optional headers for org/project scoped accounts
    organization_id: StringProperty(name="OpenAI Organization ID", default="")
    project_id: StringProperty(name="OpenAI Project ID", default="")

    # Overlay settings
    overlay_enabled: BoolProperty(name="Overlay on start", default=True)
    overlay_size: IntProperty(name="Overlay size (px)", default=160, min=64, max=512)
    overlay_margin: IntProperty(name="Overlay margin", default=24, min=0, max=300)
    overlay_bob: BoolProperty(name="Idle bob", default=True)

    def draw(self, _):
        c = self.layout.column()
        c.label(text="Chat (uses your OpenAI key)")
        c.prop(self, "api_key")
        c.prop(self, "api_base")
        c.prop(self, "model")
        c.prop(self, "system_prompt")
        r = c.row(align=True)
        r.prop(self, "temperature")
        r.prop(self, "max_tokens")
        c.prop(self, "context_messages")
        c.separator(); c.label(text="Advanced (optional)")
        c.prop(self, "organization_id")
        c.prop(self, "project_id")
        c.separator(); c.label(text="Overlay")
        c.prop(self, "overlay_enabled")
        r = c.row(align=True)
        r.prop(self, "overlay_size")
        r.prop(self, "overlay_margin")
        c.prop(self, "overlay_bob")

# ---------------------- scene properties ------------------------------------

bpy.types.Scene.clippy_input = StringProperty(name="Ask Clippy", default="")
bpy.types.Scene.clippy_messages_json = StringProperty(default="[]")
bpy.types.Scene.clippy_last_response = StringProperty(default="")
bpy.types.Scene.clippy_include_context = BoolProperty(name="Include selection context", default=True)

# Runtime overlay state
clippy_state = "idle"   # idle, thinking, talking
clippy_talk_until = 0.0

# ---------------------- logging ---------------------------------------------

def ensure_log_text():
    texts = getattr(bpy.data, "texts", None)
    if texts is None:
        return None
    t = texts.get(LOG_TEXT_NAME) or texts.new(LOG_TEXT_NAME)
    try:
        is_empty = (t.as_string() == "") if hasattr(t, "as_string") else (len(t.lines) == 0)
        if is_empty:
            t.write("# Clippy Case Study Prototype Log

")
    except Exception:
        pass
    return t


def append_log(h, body=""):
    t = ensure_log_text()
    if t is None:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    t.write(f"[{stamp}] {h}
")
    if body:
        t.write(body.replace("
", "
") + "

")

# ---------------------- OpenAI backend --------------------------------------

def _chat_completion(api_base, api_key, payload, organization_id="", project_id=""):
    api_base = api_base.strip().rstrip('/')
    api_key = api_key.strip()
    url = api_base + "/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    if organization_id:
        req.add_header("OpenAI-Organization", organization_id.strip())
    if project_id:
        req.add_header("OpenAI-Project", project_id.strip())
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ---------------------- operators -------------------------------------------

class CLIPPY_OT_Send(Operator):
    bl_idname = "clippy.send"
    bl_label = "Send to Clippy"

    def execute(self, context):
        global clippy_state, clippy_talk_until
        prefs = get_prefs(context)
        if prefs is None:
            self.report({'ERROR'}, "Preferences not available.")
            return {'CANCELLED'}
        scene = context.scene
        txt = (scene.clippy_input or "").strip()
        if not txt:
            self.report({'WARNING'}, "Type a question first.")
            return {'CANCELLED'}
        if not prefs.api_key:
            self.report({'ERROR'}, "Set your OpenAI API key in Add-ons > Cain Clippy ASCII ChatGPT Build.")
            return {'CANCELLED'}

        # history
        try:
            msgs = json.loads(scene.clippy_messages_json or "[]")
            if not isinstance(msgs, list):
                msgs = []
        except Exception:
            msgs = []

        # optional selection context
        ctx = ""
        if scene.clippy_include_context:
            sel = [o.name for o in context.selected_objects]
            act = context.active_object.name if context.active_object else "None"
            sel_str = ", ".join(sel) if sel else "None"
            ctx = "

[Context] Active: %s, Selected: %s" % (act, sel_str)

        msgs.append({"role": "user", "content": txt + ctx})
        trimmed = msgs[-max(1, prefs.context_messages):]
        payload = {
            "model": prefs.model,
            "temperature": float(prefs.temperature),
            "max_tokens": int(prefs.max_tokens),
            "messages": [{"role": "system", "content": prefs.system_prompt}] + trimmed,
        }

        # UI/overlay states
        append_log("You:", txt)
        scene.clippy_input = ""
        scene.clippy_last_response = "(thinking...)"
        clippy_state = "thinking"

        # call
        try:
            data = _chat_completion(
                prefs.api_base,
                prefs.api_key,
                payload,
                prefs.organization_id,
                prefs.project_id,
            )
            content = None
            ch = data.get("choices") if isinstance(data, dict) else None
            if isinstance(ch, list) and ch and isinstance(ch[0], dict):
                msg = ch[0].get("message")
                content = msg.get("content") if msg else None
            if not content:
                content = data.get("content") or "(empty response)"
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = str(e)
            content = f"HTTP {e.code}: {detail}"
        except Exception as e:
            content = f"Error: {e}"

        msgs.append({"role": "assistant", "content": content})
        scene.clippy_messages_json = json.dumps(msgs)
        scene.clippy_last_response = content[:4000]
        append_log("Clippy:", content)

        clippy_state = "talking"
        clippy_talk_until = time.time() + 2.0
        return {'FINISHED'}

class CLIPPY_OT_Clear(Operator):
    bl_idname = "clippy.clear"
    bl_label = "Clear Chat"
    def execute(self, context):
        context.scene.clippy_messages_json = "[]"
        context.scene.clippy_last_response = ""
        append_log("System:", "(cleared conversation)")
        return {'FINISHED'}

class CLIPPY_OT_OpenLog(Operator):
    bl_idname = "clippy.open_log"
    bl_label = "Open Log in Text Editor"
    def execute(self, _):
        ensure_log_text(); return {'FINISHED'}

# ---------------------- overlay ---------------------------------------------

import gpu
from gpu_extras.batch import batch_for_shader

overlay_handle = None
overlay_t0 = time.time()

def _ellipse(cx, cy, rx, ry, steps=84):
    pts = []
    for i in range(steps + 1):
        a = (i / steps) * (2 * math.pi)
        pts.append((cx + math.cos(a) * rx, cy + math.sin(a) * ry))
    return pts

def _circle_fan(cx, cy, r, steps=28):
    verts = [(cx, cy)]
    for i in range(steps + 1):
        a = (i / steps) * (2 * math.pi)
        verts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    return verts


def _draw_overlay():
    global clippy_state, clippy_talk_until
    prefs = get_prefs()
    if not (prefs and prefs.overlay_enabled):
        return

    # Only draw in a 3D Viewport window region
    region = getattr(bpy.context, "region", None)
    if region is None or getattr(region, "type", "") != 'WINDOW':
        return

    # size and position
    w = int(prefs.overlay_size)
    m = int(prefs.overlay_margin)
    x = region.width - w - m
    y = m
    if prefs.overlay_bob:
        y += int(6 * math.sin((time.time() - overlay_t0) * 2.3))

    # state decay
    if clippy_state == "talking" and time.time() > clippy_talk_until:
        clippy_state = "idle"

    # paperclip body
    rx, ry = w * 0.22, w * 0.40
    cx, cy = x + w * 0.68, y + w * 0.62
    outer = _ellipse(cx, cy, rx, ry)
    inner = _ellipse(cx + w * 0.045, cy + w * 0.04, rx * 0.85, ry * 0.85)

    shader = gpu.shader.from_builtin("2D_UNIFORM_COLOR")
    gpu.state.line_width_set(2.0)
    shader.bind()
    shader.uniform_float("color", (0, 0, 0, 1))
    batch_for_shader(shader, "LINE_STRIP", {"pos": outer}).draw(shader)
    batch_for_shader(shader, "LINE_STRIP", {"pos": inner}).draw(shader)

    # eyes with blink
    t = time.time() - overlay_t0
    blink = (t % 4.0) < 0.12
    if blink and clippy_state == "idle":
        eye_w, eye_h = w * 0.05, 1.0
        ex1, ey = cx - w * 0.03, cy - w * 0.06
        ex2 = cx + w * 0.05
        rect1 = [(ex1 - eye_w, ey), (ex1 + eye_w, ey), (ex1 + eye_w, ey + eye_h), (ex1 - eye_w, ey + eye_h)]
        rect2 = [(ex2 - eye_w, ey), (ex2 + eye_w, ey), (ex2 + eye_w, ey + eye_h), (ex2 - eye_w, ey + eye_h)]
        batch_for_shader(shader, "TRI_FAN", {"pos": rect1}).draw(shader)
        batch_for_shader(shader, "TRI_FAN", {"pos": rect2}).draw(shader)
    else:
        eye_l = _circle_fan(cx - w * 0.03, cy - w * 0.06, w * 0.02)
        eye_r = _circle_fan(cx + w * 0.05, cy - w * 0.06, w * 0.02)
        batch_for_shader(shader, "TRI_FAN", {"pos": eye_l}).draw(shader)
        batch_for_shader(shader, "TRI_FAN", {"pos": eye_r}).draw(shader)

    # state indicators
    if clippy_state == "thinking":
        dy = w * 0.18
        for i in range(3):
            dot = _circle_fan(cx - w * 0.15 + i * w * 0.08, cy + dy, w * 0.014)
            batch_for_shader(shader, "TRI_FAN", {"pos": dot}).draw(shader)
    elif clippy_state == "talking":
        mouth = _ellipse(cx + w * 0.01, cy - w * 0.01, w * 0.04, w * 0.02, steps=28)
        batch_for_shader(shader, "LINE_STRIP", {"pos": mouth}).draw(shader)

# ---------------------- UI panel --------------------------------------------

class CLIPPY_PT_Panel(Panel):
    bl_label = "Clippy"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Clippy'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        prefs = get_prefs(context)

        head = layout.box()
        r = head.row(align=True)
        r.label(text="Clippy ready", icon='HELP')
        r.operator(CLIPPY_OT_Clear.bl_idname, text="Clear", icon='TRASH')
        r.operator(CLIPPY_OT_OpenLog.bl_idname, text="Log", icon='TEXT')

        box = layout.box()
        box.label(text="Ask a question")
        box.prop(scene, "clippy_include_context")
        row = box.row(align=True)
        row.prop(scene, "clippy_input", text="")
        row.operator(CLIPPY_OT_Send.bl_idname, text="Send", icon='PLAY')

        layout.label(text="Last reply:")
        preview = scene.clippy_last_response or "(none yet)"
        for line in _wrap(preview, 88):
            layout.label(text=line)

        layout.separator()
        rr = layout.row(align=True)
        rr.label(text=f"Model: {prefs.model}")

        ov = layout.box()
        ov.label(text="Avatar Overlay (visible only in a 3D Viewport)")
        ro = ov.row(align=True)
        ro.operator(CLIPPY_OT_ToggleOverlay.bl_idname, text="Toggle", icon='HIDE_OFF')
        ro = ov.row(align=True)
        ro.prop(prefs, "overlay_size")
        ro.prop(prefs, "overlay_margin")
        ov.prop(prefs, "overlay_bob")

# ---------------------- overlay toggle op -----------------------------------

class CLIPPY_OT_ToggleOverlay(Operator):
    bl_idname = "clippy.toggle_overlay"
    bl_label = "Toggle Overlay"

    def execute(self, context):
        global overlay_handle
        prefs = get_prefs(context)
        prefs.overlay_enabled = not prefs.overlay_enabled
        if prefs.overlay_enabled and overlay_handle is None:
            overlay_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), 'WINDOW', 'POST_PIXEL')
            self.report({'INFO'}, "Overlay: ON")
        elif not prefs.overlay_enabled and overlay_handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(overlay_handle, 'WINDOW')
            overlay_handle = None
            self.report({'INFO'}, "Overlay: OFF")
        return {'FINISHED'}

# ---------------------- helpers ---------------------------------------------

def _wrap(s, width):
    words = s.split()
    line = []
    n = 0
    for w in words:
        add = len(w) + (1 if line else 0)
        if n + add > width:
            yield ' '.join(line)
            line = [w]
            n = len(w)
        else:
            line.append(w)
            n += add
    if line:
        yield ' '.join(line)

# ---------------------- registration ----------------------------------------

classes = (
    CLIPPY_Prefs,
    CLIPPY_OT_Send,
    CLIPPY_OT_Clear,
    CLIPPY_OT_OpenLog,
    CLIPPY_PT_Panel,
    CLIPPY_OT_ToggleOverlay,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    prefs = get_prefs()
    if prefs and prefs.overlay_enabled:
        global overlay_handle
        if overlay_handle is None:
            overlay_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), 'WINDOW', 'POST_PIXEL')

def unregister():
    global overlay_handle
    if overlay_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(overlay_handle, 'WINDOW')
        except Exception:
            pass
        overlay_handle = None
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

if __name__ == "__main__":
    register()
