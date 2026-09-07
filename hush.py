"""
hush — the design system behind Khachiwhisper's interface.

The idea: a quiet, dark, tactile surface, like a studio console at night. Deep ink layers,
warm off-white type, and exactly one live colour, coral, for anything that's recording,
selected or clickable. Mint means "ready". Nothing here uses an AppKit bezel; every control is
drawn from the tokens below, so the app looks the same on every macOS version and theme.

    Tokens        C (colour), F (type scale), S (spacing), R (radii)
    Primitives    HView (flipped, layer-backed), Interactive (hover / press), draw_text
    Components    Surface, Label, Divider, Button, Select, Toggle, Keycap, RecorderButton,
                  Pill, ProgressBar, NavItem, StatusDot, WindowControls, make_window
"""

import objc
from AppKit import (
    NSAppearance,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSCursor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSImage,
    NSImageView,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakePoint, NSMakeRect, NSPointInRect
from Quartz import CABasicAnimation, CALayer, CAMediaTimingFunction, CATransaction

# --------------------------------------------------------------------------- tokens


def rgba(hexstr: str, a: float = 1.0) -> NSColor:
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


class C:
    """Colour. Ink surfaces get lighter as they come forward; text is warm, never pure white."""
    INK0 = rgba("#121316")          # window
    INK1 = rgba("#191A1E")          # sidebar
    INK2 = rgba("#202126")          # card
    INK3 = rgba("#2A2B31")          # control
    INK4 = rgba("#35363D")          # control, hovered
    LINE = rgba("#FFFFFF", 0.08)
    LINE_STRONG = rgba("#FFFFFF", 0.14)
    TEXT = rgba("#F2F1EC")
    TEXT2 = rgba("#F2F1EC", 0.62)
    TEXT3 = rgba("#F2F1EC", 0.40)
    ACCENT = rgba("#FF7A59")        # coral: live, selected, primary action
    ACCENT_HOVER = rgba("#FF8F73")
    ACCENT_PRESS = rgba("#E96A4B")
    ACCENT_SOFT = rgba("#FF7A59", 0.16)
    ACCENT_LINE = rgba("#FF7A59", 0.55)
    ON_ACCENT = rgba("#1A0E0A")
    OK = rgba("#7ED9A5")            # mint: ready
    OK_SOFT = rgba("#7ED9A5", 0.14)
    WARN = rgba("#F0B95C")
    WARN_SOFT = rgba("#F0B95C", 0.14)
    DANGER = rgba("#FF6B6B")
    DANGER_SOFT = rgba("#FF6B6B", 0.14)
    HOVER = rgba("#FFFFFF", 0.05)
    PRESS = rgba("#000000", 0.18)


class F:
    """Type scale. SF via the system font, one monospaced face for keys."""
    @staticmethod
    def get(style: str) -> NSFont:
        return {
            "display": NSFont.systemFontOfSize_weight_(24, NSFontWeightSemibold),
            "title": NSFont.systemFontOfSize_weight_(17, NSFontWeightSemibold),
            "heading": NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold),
            "body": NSFont.systemFontOfSize_weight_(13, NSFontWeightRegular),
            "label": NSFont.systemFontOfSize_weight_(12.5, NSFontWeightMedium),
            "caption": NSFont.systemFontOfSize_weight_(11, NSFontWeightRegular),
            "eyebrow": NSFont.systemFontOfSize_weight_(10.5, NSFontWeightBold),
            "mono": NSFont.monospacedSystemFontOfSize_weight_(12, NSFontWeightMedium),
            "mono-lg": NSFont.monospacedSystemFontOfSize_weight_(13, NSFontWeightMedium),
        }[style]


class S:
    XS, SM, MD, LG, XL, XXL = 4, 8, 12, 16, 24, 32


class R:
    CONTROL, CARD, PILL = 8, 12, 999


# --------------------------------------------------------------------------- primitives


def rounded(rect, radius):
    return NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)


def fill(rect, color, radius=0):
    color.set()
    (rounded(rect, radius) if radius else NSBezierPath.bezierPathWithRect_(rect)).fill()


def stroke(rect, color, radius=0, width=1.0):
    """Inset hairline stroke so it stays crisp at 1px."""
    color.set()
    inset = NSMakeRect(rect.origin.x + width / 2, rect.origin.y + width / 2,
                       rect.size.width - width, rect.size.height - width)
    p = rounded(inset, max(0, radius - width / 2)) if radius else NSBezierPath.bezierPathWithRect_(inset)
    p.setLineWidth_(width)
    p.stroke()


def attributed(text, style="body", color=None, align="left", wrap=False):
    para = NSMutableParagraphStyle.alloc().init()
    para.setAlignment_({"left": 0, "right": 2, "center": 1}[align])
    para.setLineBreakMode_(0 if wrap else 4)  # word wrap / truncate tail
    return NSAttributedString.alloc().initWithString_attributes_(text, {
        NSFontAttributeName: F.get(style),
        NSForegroundColorAttributeName: color or C.TEXT,
        NSParagraphStyleAttributeName: para,
    })


def draw_text(text, rect, style="body", color=None, align="left", valign="center", wrap=False):
    s = attributed(text, style, color, align, wrap)
    if wrap:
        s.drawInRect_(rect)
        return s.size()
    size = s.size()
    y = rect.origin.y + (rect.size.height - size.height) / 2 if valign == "center" else rect.origin.y
    s.drawInRect_(NSMakeRect(rect.origin.x, y, rect.size.width, size.height))
    return size


def text_width(text, style="body"):
    return attributed(text, style).size().width


def text_height(text, width, style="body"):
    s = attributed(text, style, wrap=True)
    return s.boundingRectWithSize_options_context_((width, 100000), 1, None).size.height


class HView(NSView):
    """Top-left origin, layer-backed. All Hush views inherit from this."""

    def initWithFrame_(self, frame):
        self = objc.super(HView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        return self

    def isFlipped(self):
        return True


class Interactive(HView):
    """Hover and press tracking with a Python callback."""

    def initWithFrame_(self, frame):
        self = objc.super(Interactive, self).initWithFrame_(frame)
        if self is None:
            return None
        self.hover = False
        self.pressed = False
        self.enabled = True
        self.on_click = None
        self._area = None
        return self

    def updateTrackingAreas(self):
        objc.super(Interactive, self).updateTrackingAreas()
        if self._area is not None:
            self.removeTrackingArea_(self._area)
        self._area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect,
            self, None)
        self.addTrackingArea_(self._area)

    def acceptsFirstMouse_(self, event):
        return True

    def mouseEntered_(self, event):
        self.hover = True
        if self.enabled:
            NSCursor.pointingHandCursor().set()
        self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        self.hover = False
        self.pressed = False
        NSCursor.arrowCursor().set()
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        if not self.enabled:
            return
        self.pressed = True
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        was = self.pressed
        self.pressed = False
        self.setNeedsDisplay_(True)
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        if was and self.enabled and NSPointInRect(p, self.bounds()):
            self.click()

    def click(self):
        if self.on_click:
            self.on_click()

    def setEnabled_(self, on):
        self.enabled = bool(on)
        self.setNeedsDisplay_(True)


# --------------------------------------------------------------------------- components


class Surface(HView):
    """A card: raised ink with a hairline edge."""

    def drawRect_(self, rect):
        b = self.bounds()
        fill(b, C.INK2, R.CARD)
        stroke(b, C.LINE, R.CARD)


class Label(HView):
    def initWithFrame_(self, frame):
        self = objc.super(Label, self).initWithFrame_(frame)
        if self is None:
            return None
        self.text, self.style, self.color, self.align, self.wrap = "", "body", None, "left", False
        return self

    @objc.python_method
    def set(self, text=None, color=None):
        if text is not None:
            self.text = text
        if color is not None:
            self.color = color
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        draw_text(self.text, self.bounds(), self.style, self.color, self.align,
                  "top" if self.wrap else "center", self.wrap)


def label(parent, text, x, y, w, h=18, style="body", color=None, align="left", wrap=False):
    v = Label.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.text, v.style, v.color, v.align, v.wrap = text, style, color, align, wrap
    parent.addSubview_(v)
    return v


def eyebrow(parent, text, x, y, w):
    return label(parent, text.upper(), x, y, w, 14, "eyebrow", C.TEXT3)


class Divider(HView):
    def drawRect_(self, rect):
        fill(self.bounds(), C.LINE)


def divider(parent, x, y, w):
    v = Divider.alloc().initWithFrame_(NSMakeRect(x, y, w, 1))
    parent.addSubview_(v)
    return v


class Button(Interactive):
    """kind: primary (coral), tonal (raised ink), ghost (text only), danger."""

    def initWithFrame_(self, frame):
        self = objc.super(Button, self).initWithFrame_(frame)
        if self is None:
            return None
        self.title, self.kind = "", "tonal"
        return self

    def setTitle_(self, t):
        self.title = t
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        b = self.bounds()
        if self.kind == "primary":
            bg = C.ACCENT_PRESS if self.pressed else C.ACCENT_HOVER if self.hover else C.ACCENT
            fill(b, bg, R.CONTROL)
            fg = C.ON_ACCENT
        elif self.kind == "tonal":
            fill(b, C.INK4 if self.hover and not self.pressed else C.INK3, R.CONTROL)
            if self.pressed:
                fill(b, C.PRESS, R.CONTROL)
            stroke(b, C.LINE_STRONG, R.CONTROL)
            fg = C.TEXT
        elif self.kind == "danger":
            fill(b, C.DANGER_SOFT, R.CONTROL)
            stroke(b, rgba("#FF6B6B", 0.35), R.CONTROL)
            if self.hover:
                fill(b, C.HOVER, R.CONTROL)
            fg = C.DANGER
        else:  # ghost
            if self.hover:
                fill(b, C.HOVER, R.CONTROL)
            if self.pressed:
                fill(b, C.PRESS, R.CONTROL)
            fg = C.TEXT2 if not self.hover else C.TEXT
        if not self.enabled:
            fg = fg.colorWithAlphaComponent_(0.4)
        draw_text(self.title, b, "label", fg, "center")


def button(parent, title, x, y, w=None, h=28, kind="tonal", on_click=None):
    w = w or int(text_width(title, "label") + 28)
    v = Button.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.title, v.kind, v.on_click = title, kind, on_click
    parent.addSubview_(v)
    return v


class Select(Interactive):
    """A dropdown drawn in Hush; the transient menu itself is the system one."""

    def initWithFrame_(self, frame):
        self = objc.super(Select, self).initWithFrame_(frame)
        if self is None:
            return None
        self.items = []      # (title, value)
        self.value = None
        self.on_change = None
        return self

    @objc.python_method
    def current_title(self):
        for t, v in self.items:
            if v == self.value:
                return t
        return "—"

    def drawRect_(self, rect):
        b = self.bounds()
        fill(b, C.INK4 if self.hover else C.INK3, R.CONTROL)
        stroke(b, C.LINE_STRONG, R.CONTROL)
        fg = C.TEXT if self.enabled else C.TEXT3
        draw_text(self.current_title(), NSMakeRect(12, 0, b.size.width - 36, b.size.height), "label", fg)
        # chevron
        cx, cy = b.size.width - 16, b.size.height / 2
        p = NSBezierPath.bezierPath()
        p.moveToPoint_(NSMakePoint(cx - 4, cy - 2))
        p.lineToPoint_(NSMakePoint(cx, cy + 2))
        p.lineToPoint_(NSMakePoint(cx + 4, cy - 2))
        p.setLineWidth_(1.5)
        p.setLineCapStyle_(1)
        C.TEXT2.set()
        p.stroke()

    def click(self):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for t, v in self.items:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(t, "pick:", "")
            mi.setTarget_(self)
            mi.setRepresentedObject_(v)
            mi.setState_(1 if v == self.value else 0)
            menu.addItem_(mi)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(0, self.bounds().size.height + 4), self)

    def pick_(self, sender):
        v = sender.representedObject()
        if v != self.value:
            self.value = v
            self.setNeedsDisplay_(True)
            if self.on_change:
                self.on_change(v)


def select(parent, items, value, x, y, w, h=28, on_change=None):
    v = Select.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.items, v.value, v.on_change = list(items), value, on_change
    parent.addSubview_(v)
    return v


class Toggle(Interactive):
    W, H, KNOB = 38, 22, 18

    def initWithFrame_(self, frame):
        self = objc.super(Toggle, self).initWithFrame_(frame)
        if self is None:
            return None
        self.on = False
        self.on_change = None
        self.track = CALayer.layer()
        self.track.setFrame_(((0, 0), (self.W, self.H)))
        self.track.setCornerRadius_(self.H / 2)
        self.knob = CALayer.layer()
        self.knob.setFrame_(((2, 2), (self.KNOB, self.KNOB)))
        self.knob.setCornerRadius_(self.KNOB / 2)
        self.knob.setBackgroundColor_(rgba("#FFFFFF").CGColor())
        self.knob.setShadowOpacity_(0.35)
        self.knob.setShadowRadius_(2)
        self.knob.setShadowOffset_((0, 1))
        self.layer().addSublayer_(self.track)
        self.layer().addSublayer_(self.knob)
        self._apply(animated=False)
        return self

    @objc.python_method
    def _apply(self, animated=True):
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.18 if animated else 0.0)
        CATransaction.setAnimationTimingFunction_(CAMediaTimingFunction.functionWithName_("easeInEaseOut"))
        self.track.setBackgroundColor_((C.ACCENT if self.on else C.INK4).CGColor())
        self.track.setBorderColor_((C.ACCENT if self.on else C.LINE_STRONG).CGColor())
        self.track.setBorderWidth_(0 if self.on else 1)
        x = self.W - self.KNOB - 2 if self.on else 2
        self.knob.setFrame_(((x, 2), (self.KNOB, self.KNOB)))
        self.track.setOpacity_(1.0 if self.enabled else 0.4)
        CATransaction.commit()

    @objc.python_method
    def set_on(self, on, animated=True):
        self.on = bool(on)
        self._apply(animated)

    def setEnabled_(self, on):
        self.enabled = bool(on)
        self._apply(False)

    def click(self):
        self.set_on(not self.on)
        if self.on_change:
            self.on_change(self.on)


def toggle(parent, on, x, y, on_change=None):
    v = Toggle.alloc().initWithFrame_(NSMakeRect(x, y, Toggle.W, Toggle.H))
    v.on_change = on_change
    v.set_on(on, animated=False)
    parent.addSubview_(v)
    return v


def draw_keycap(b, text, style="mono", fg=None, bg=None, border=None, accent=False):
    fill(b, bg or C.INK3, 6)
    # a darker lip along the bottom edge gives it a little physical depth
    fill(NSMakeRect(b.origin.x + 1, b.origin.y + b.size.height - 2, b.size.width - 2, 1), rgba("#000000", 0.35))
    stroke(b, C.ACCENT_LINE if accent else (border or C.LINE_STRONG), 6)
    draw_text(text, b, style, fg or C.TEXT, "center")


class Keycap(HView):
    def initWithFrame_(self, frame):
        self = objc.super(Keycap, self).initWithFrame_(frame)
        if self is None:
            return None
        self.text, self.dim = "", False
        return self

    def drawRect_(self, rect):
        draw_keycap(self.bounds(), self.text, "mono", C.TEXT2 if self.dim else C.TEXT)


def keycap(parent, text, x, y, h=24, dim=False):
    w = max(28, int(text_width(text, "mono") + 16))
    v = Keycap.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.text, v.dim = text, dim
    parent.addSubview_(v)
    return v


class RecorderButton(Interactive):
    """Shows the current shortcut as a keycap; while recording it glows coral and pulses."""

    def initWithFrame_(self, frame):
        self = objc.super(RecorderButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.text = ""
        self.recording = False
        self.ring = CALayer.layer()
        self.ring.setFrame_(((0, 0), (frame.size.width, frame.size.height)))
        self.ring.setCornerRadius_(R.CONTROL)
        self.ring.setBorderWidth_(1.5)
        self.ring.setBorderColor_(C.ACCENT.CGColor())
        self.ring.setOpacity_(0.0)
        self.layer().addSublayer_(self.ring)
        return self

    def setRecording_(self, on):
        self.recording = bool(on)
        self.ring.removeAllAnimations()
        if on:
            pulse = CABasicAnimation.animationWithKeyPath_("opacity")
            pulse.setFromValue_(1.0)
            pulse.setToValue_(0.25)
            pulse.setDuration_(0.7)
            pulse.setAutoreverses_(True)
            pulse.setRepeatCount_(1e9)
            self.ring.addAnimation_forKey_(pulse, "pulse")
            self.ring.setOpacity_(1.0)
        else:
            self.ring.setOpacity_(0.0)
        self.setNeedsDisplay_(True)

    def setText_(self, t):
        self.text = t
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        b = self.bounds()
        if self.recording:
            draw_keycap(b, "Type shortcut…", "label", C.ACCENT, C.ACCENT_SOFT, C.ACCENT_LINE)
        else:
            draw_keycap(b, self.text, "mono-lg", C.TEXT, C.INK4 if self.hover else C.INK3)


class Pill(HView):
    TONES = {
        "ok": (C.OK_SOFT, C.OK), "accent": (C.ACCENT_SOFT, C.ACCENT), "warn": (C.WARN_SOFT, C.WARN),
        "danger": (C.DANGER_SOFT, C.DANGER), "neutral": (rgba("#FFFFFF", 0.07), C.TEXT2),
    }

    def initWithFrame_(self, frame):
        self = objc.super(Pill, self).initWithFrame_(frame)
        if self is None:
            return None
        self.text, self.tone = "", "neutral"
        return self

    @objc.python_method
    def set(self, text, tone=None):
        self.text = text
        if tone:
            self.tone = tone
        w = max(0, int(text_width(text, "caption") + 22)) if text else 0
        f = self.frame()
        # keep the right edge anchored
        self.setFrame_(NSMakeRect(f.origin.x + f.size.width - w, f.origin.y, w, f.size.height))
        self.setHidden_(not text)
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        if not self.text:
            return
        bg, fg = self.TONES[self.tone]
        b = self.bounds()
        fill(b, bg, R.PILL)
        draw_text(self.text, b, "caption", fg, "center")


def pill(parent, text, right_x, y, tone="neutral", h=20):
    v = Pill.alloc().initWithFrame_(NSMakeRect(right_x, y, 0, h))
    v.tone = tone
    parent.addSubview_(v)
    v.set(text, tone)
    return v


class ProgressBar(HView):
    def initWithFrame_(self, frame):
        self = objc.super(ProgressBar, self).initWithFrame_(frame)
        if self is None:
            return None
        self.value = 0.0
        return self

    def setValue_(self, v):
        self.value = max(0.0, min(1.0, float(v)))
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        b = self.bounds()
        fill(b, C.LINE_STRONG, b.size.height / 2)
        w = max(b.size.height, b.size.width * self.value)
        fill(NSMakeRect(0, 0, w, b.size.height), C.ACCENT, b.size.height / 2)


def progress(parent, x, y, w, h=4):
    v = ProgressBar.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    parent.addSubview_(v)
    return v


class NavItem(Interactive):
    def initWithFrame_(self, frame):
        self = objc.super(NavItem, self).initWithFrame_(frame)
        if self is None:
            return None
        self.title, self.selected = "", False
        self.icon = NSImageView.alloc().initWithFrame_(NSMakeRect(12, (frame.size.height - 16) / 2, 18, 16))
        self.addSubview_(self.icon)
        return self

    def setSymbol_(self, name):
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        self.icon.setImage_(img)
        self._tint()

    def _tint(self):
        self.icon.setContentTintColor_(C.ACCENT if self.selected else C.TEXT2)

    def setSelected_(self, on):
        self.selected = bool(on)
        self._tint()
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        b = self.bounds()
        if self.selected:
            fill(b, C.ACCENT_SOFT, R.CONTROL)
        elif self.hover:
            fill(b, C.HOVER, R.CONTROL)
        draw_text(self.title, NSMakeRect(38, 0, b.size.width - 44, b.size.height), "label",
                  C.ACCENT if self.selected else C.TEXT2 if not self.hover else C.TEXT)


def nav_item(parent, symbol, title, x, y, w, h=32, on_click=None):
    v = NavItem.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.title, v.on_click = title, on_click
    v.setSymbol_(symbol)
    parent.addSubview_(v)
    return v


class StatusDot(HView):
    def initWithFrame_(self, frame):
        self = objc.super(StatusDot, self).initWithFrame_(frame)
        if self is None:
            return None
        self.color = C.OK
        return self

    def drawRect_(self, rect):
        b = self.bounds()
        fill(NSMakeRect(b.size.width / 2 - 4, b.size.height / 2 - 4, 8, 8), self.color, 4)
        fill(NSMakeRect(b.size.width / 2 - 6, b.size.height / 2 - 6, 12, 12), self.color.colorWithAlphaComponent_(0.18), 6)


class WindowControls(HView):
    """Two hand-drawn window buttons: close (coral) and minimise (amber)."""

    D, GAP = 12, 8

    def initWithFrame_(self, frame):
        self = objc.super(WindowControls, self).initWithFrame_(frame)
        if self is None:
            return None
        self.hover = False
        self._area = None
        return self

    def updateTrackingAreas(self):
        objc.super(WindowControls, self).updateTrackingAreas()
        if self._area is not None:
            self.removeTrackingArea_(self._area)
        self._area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect,
            self, None)
        self.addTrackingArea_(self._area)

    def mouseEntered_(self, event):
        self.hover = True
        self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        self.hover = False
        self.setNeedsDisplay_(True)

    def _rects(self):
        cy = self.bounds().size.height / 2
        return [NSMakeRect(0, cy - self.D / 2, self.D, self.D),
                NSMakeRect(self.D + self.GAP, cy - self.D / 2, self.D, self.D)]

    def drawRect_(self, rect):
        close, mini = self._rects()
        for r, color in ((close, C.ACCENT), (mini, C.WARN)):
            fill(r, color if self.hover else C.INK4, self.D / 2)
            stroke(r, rgba("#000000", 0.25), self.D / 2)
        if self.hover:
            rgba("#1A0E0A", 0.8).set()
            p = NSBezierPath.bezierPath()
            p.setLineWidth_(1.5)
            cx, cy = close.origin.x + self.D / 2, close.origin.y + self.D / 2
            p.moveToPoint_(NSMakePoint(cx - 2.5, cy - 2.5)); p.lineToPoint_(NSMakePoint(cx + 2.5, cy + 2.5))
            p.moveToPoint_(NSMakePoint(cx + 2.5, cy - 2.5)); p.lineToPoint_(NSMakePoint(cx - 2.5, cy + 2.5))
            mx, my = mini.origin.x + self.D / 2, mini.origin.y + self.D / 2
            p.moveToPoint_(NSMakePoint(mx - 3, my)); p.lineToPoint_(NSMakePoint(mx + 3, my))
            p.stroke()

    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        close, mini = self._rects()
        if NSPointInRect(p, close):
            self.window().close()
        elif NSPointInRect(p, mini):
            self.window().miniaturize_(None)


class Root(HView):
    def drawRect_(self, rect):
        fill(self.bounds(), C.INK0)


def make_window(width, height, title):
    """A window with no system chrome: hidden title bar and buttons, Hush ink background,
    draggable anywhere. Returns (window, root view)."""
    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
             | NSWindowStyleMaskFullSizeContentView)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False)
    win.setTitle_(title)
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(1)
    win.setMovableByWindowBackground_(True)
    win.setReleasedWhenClosed_(False)
    win.setBackgroundColor_(C.INK0)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
    for i in (0, 1, 2):  # close, minimise, zoom
        b = win.standardWindowButton_(i)
        if b is not None:
            b.setHidden_(True)
    root = Root.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    win.setContentView_(root)
    controls = WindowControls.alloc().initWithFrame_(NSMakeRect(18, 14, 40, 18))
    root.addSubview_(controls)
    return win, root
