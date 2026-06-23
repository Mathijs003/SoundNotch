import threading
import time
import urllib.request
from AppKit import (
    NSPanel, NSView, NSTextField, NSImageView, NSImage, NSButton, NSTimer,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSBackingStoreBuffered,
    NSColor, NSFont, NSMakeRect, NSScreen, NSEvent, NSBezierPath,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSLineBreakByTruncatingTail, NSTextAlignmentLeft,
    NSImageScaleProportionallyUpOrDown,
    NSScreenSaverWindowLevel,
    NSViewWidthSizable, NSViewHeightSizable,
)
from Foundation import NSObject, NSData
from Quartz import CGDisplayIsBuiltin

# ── Notch geometry (measured on the built-in display: 220 × 38 pt) ──────────────
NOTCH_W  = 220.0
NOTCH_HW = NOTCH_W / 2.0      # 110

H_MINI   = 38.0              # collapsed height = notch height
H_FULL   = 112.0            # expanded height (room for the control row)
FLARE    = 22.0             # top inverted-fillet radius (also side margin)
CR_BOT   = 22.0             # bottom convex corner radius

# Collapsed panel
WW_C     = 348.0            # collapsed width  (art | notch | fwd)
ART_C    = 24.0            # collapsed art size
ART_C_X  = 32.0            # collapsed art left x (just left of the notch)

# Expanded panel
WW_E     = 420.0            # expanded width
ART_E    = 60.0            # expanded art size
ART_E_X  = 28.0            # expanded art left x (near the panel's left edge)

FWD_SZ      = 20.0
GAP_NOTCH   = 8.0          # gap between art/fwd and the notch
TEXT_X      = ART_E_X + ART_E + 12.0   # 100 — title starts here, under the notch
TITLE_YTOP  = 40.0
ARTIST_YTOP = 60.0

# Expanded control row (⏮ ⏯ ⏭) — centred in the widget; ⏭ is the same view as
# the collapsed forward button, animating down into this row.
CTRL_SZ   = 22.0          # prev / next button size (expanded)
PP_SZ     = 26.0          # play-pause button size
ROW_CY    = 92.0          # control-row centre, measured from the panel top
_ROW_GAP  = 64.0          # centre-to-centre spacing
PP_CX     = WW_E / 2.0    # 210 — centred in the widget
PREV_CX   = PP_CX - _ROW_GAP
NEXT_CX   = PP_CX + _ROW_GAP

ANIM_DUR  = 0.30           # expand / collapse duration (s)
ANIM_FADE = 0.18           # show / hide fade duration (s)
ANIM_PEEK = 0.16           # peek / unpeek duration (s)
PEEK_T    = 0.20           # interpolation value for the "slight" hover peek
TICK      = 1.0 / 60.0

NOTCH_ZONE_HALF_W = 160
NOTCH_ZONE_H      = 50

# ── States ─────────────────────────────────────────────────────────────────────
HIDDEN    = 'hidden'
COLLAPSED = 'collapsed'
EXPANDED  = 'expanded'


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _lerp(a, b, t):
    return a + (b - a) * t


def _ease(p):
    """easeInOutCubic"""
    if p < 0.5:
        return 4.0 * p * p * p
    f = -2.0 * p + 2.0
    return 1.0 - (f * f * f) / 2.0


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _builtin_screen():
    for s in NSScreen.screens():
        did = s.deviceDescription().get('NSScreenNumber', 0)
        if CGDisplayIsBuiltin(did):
            return s
    return NSScreen.mainScreen()


def _in_notch_zone(loc):
    sf  = _builtin_screen().frame()
    cx  = sf.origin.x + sf.size.width / 2
    top = sf.origin.y + sf.size.height
    # Bounded both below AND above: a display placed *above* the built-in screen
    # lives at y > top, so without the upper bound any cursor up there (near the
    # notch's x) would falsely trigger the zone.
    return (
        abs(loc.x - cx) < NOTCH_ZONE_HALF_W
        and top - NOTCH_ZONE_H <= loc.y <= top
    )


# ── Main-thread dispatch ───────────────────────────────────────────────────────

class _Runner(NSObject):
    def run_(self, _):
        if hasattr(self, '_fn'):
            self._fn()
            self._self_ref = None


def run_on_main(fn):
    r = _Runner.alloc().init()
    r._fn = fn
    r._self_ref = r
    r.performSelectorOnMainThread_withObject_waitUntilDone_('run:', None, False)


# ── Button handler ─────────────────────────────────────────────────────────────

class _BtnHandler(NSObject):
    def act_(self, sender):
        if hasattr(self, 'fn') and self.fn:
            self.fn()


class _OverlayButton(NSButton):
    """Responds to the first click even when the panel isn't key."""
    def acceptsFirstMouse_(self, event):
        return True


def _label(frame, font, color):
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setStringValue_('')
    f.setFont_(font)
    f.setTextColor_(color)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setAlignment_(NSTextAlignmentLeft)
    f.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
    return f


# ── Background shape ───────────────────────────────────────────────────────────

class _NotchBG(NSView):
    """Pitch-black panel shaped like the MacBook notch: flush full-width top edge,
    smooth inverted (concave) top corners that flare outward into the screen, and
    convex bottom corners. Redrawn from its bounds every animation frame."""

    def isFlipped(self):
        return False

    def drawRect_(self, rect):
        b  = self.bounds()
        w  = b.size.width
        h  = b.size.height
        L  = FLARE
        R  = w - FLARE
        ft = FLARE
        rb = min(CR_BOT, max(0.0, h - ft))

        p = NSBezierPath.bezierPath()
        p.moveToPoint_((0.0, h))
        p.lineToPoint_((w, h))
        p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (w, h - ft), ft, 90, 180, False)
        p.lineToPoint_((R, rb))
        p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (R - rb, rb), rb, 0, -90, True)
        p.lineToPoint_((L + rb, 0.0))
        p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (L + rb, rb), rb, 270, 180, True)
        p.lineToPoint_((L, h - ft))
        p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (0.0, h - ft), ft, 0, 90, False)
        p.closePath()
        NSColor.blackColor().set()
        p.fill()


# ── NotchWindow ────────────────────────────────────────────────────────────────

class NotchWindow:

    def __init__(self, on_prev=None, on_play_pause=None, on_next=None,
                 on_vol_down=None, on_vol_up=None, on_seek=None):
        self._on_prev       = on_prev
        self._on_play_pause = on_play_pause
        self._on_next       = on_next
        self._state   = HIDDEN
        self._handlers = []

        # Animated scalars
        self._t       = 0.0     # 0 = collapsed, 1 = expanded
        self._alpha   = 0.0     # window opacity (show / hide)
        self._anim    = {}      # name -> (from, target, start, dur)
        self._timer   = None

        w, h = WW_C, H_MINI
        sf = _builtin_screen().frame()
        x = sf.origin.x + (sf.size.width - w) / 2
        y = sf.origin.y + sf.size.height - h

        self._win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self._win.setFloatingPanel_(True)
        self._win.setBecomesKeyOnlyIfNeeded_(True)
        self._win.setOpaque_(False)
        self._win.setBackgroundColor_(NSColor.clearColor())
        self._win.setLevel_(NSScreenSaverWindowLevel)
        self._win.setIgnoresMouseEvents_(False)
        self._win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        self._win.setHasShadow_(False)
        self._win.setAlphaValue_(0.0)

        cv = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))

        self._bg = _NotchBG.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self._bg.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        cv.addSubview_(self._bg)

        # Single shared album art (grows + slides left on expand)
        self._art = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ART_C, ART_C))
        self._art.setWantsLayer_(True)
        self._art.layer().setCornerRadius_(5.0)
        self._art.layer().setMasksToBounds_(True)
        self._art.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self._art.setContentTintColor_(NSColor.colorWithWhite_alpha_(1.0, 0.5))
        self._art.setImage_(self._placeholder())
        cv.addSubview_(self._art)

        # Title + artist — appear under the notch when expanded
        self._title_lbl = _label(
            NSMakeRect(0, 0, 10, 20),
            NSFont.boldSystemFontOfSize_(14),
            NSColor.whiteColor(),
        )
        self._title_lbl.setAlphaValue_(0.0)
        cv.addSubview_(self._title_lbl)

        self._artist_lbl = _label(
            NSMakeRect(0, 0, 10, 18),
            NSFont.systemFontOfSize_(12),
            NSColor.colorWithWhite_alpha_(1.0, 0.55),
        )
        self._artist_lbl.setAlphaValue_(0.0)
        cv.addSubview_(self._artist_lbl)

        # Control row. Prev + play/pause fade in when expanded; the forward
        # button is shared with the collapsed bar and animates down into the row.
        white = NSColor.colorWithWhite_alpha_(1.0, 0.95)
        self._prev_btn = _btn_make('backward.fill', white, self._prev, self._handlers)
        self._prev_btn.setAlphaValue_(0.0)
        cv.addSubview_(self._prev_btn)

        self._pp_btn = _btn_make('pause.fill', white, self._pp, self._handlers)
        self._pp_btn.setAlphaValue_(0.0)
        cv.addSubview_(self._pp_btn)

        self._fwd = _btn_make('forward.fill', NSColor.colorWithWhite_alpha_(1.0, 0.85),
                              self._next, self._handlers)
        cv.addSubview_(self._fwd)

        self._win.setContentView_(cv)
        self._apply_layout()
        self._win.orderFrontRegardless()

    # ── Layout (drives everything from self._t and self._alpha) ────────────────

    def _apply_layout(self):
        t = self._t
        w = _lerp(WW_C, WW_E, t)
        h = _lerp(H_MINI, H_FULL, t)

        sf = _builtin_screen().frame()
        x = sf.origin.x + (sf.size.width - w) / 2
        y = sf.origin.y + sf.size.height - h
        self._win.setFrame_display_(NSMakeRect(x, y, w, h), False)
        self._win.setAlphaValue_(self._alpha)

        ncx    = w / 2.0
        nright = ncx + NOTCH_HW

        # Album art — grows and slides left, anchored to the top
        asz   = _lerp(ART_C, ART_E, t)
        ax    = _lerp(ART_C_X, ART_E_X, t)
        aytop = _lerp((H_MINI - ART_C) / 2.0, (H_FULL - ART_E) / 2.0, t)
        self._art.setFrame_(NSMakeRect(ax, h - aytop - asz, asz, asz))
        self._art.layer().setCornerRadius_(_lerp(5.0, 10.0, t))

        # Forward button — holds at notch level through the whole peek (so it
        # stays an easy, stationary click target) and only drops into the control
        # row once the full expand begins. te is 0 until t passes the peek.
        te = _clamp01((t - PEEK_T) / (1.0 - PEEK_T))
        fwd_sz  = _lerp(FWD_SZ, CTRL_SZ, te)
        fwd_cx  = _lerp(nright + GAP_NOTCH + FWD_SZ / 2.0, NEXT_CX, te)
        fwd_cyt = _lerp(H_MINI / 2.0, ROW_CY, te)          # centre, from top
        _set_centered(self._fwd, fwd_cx, h - fwd_cyt, fwd_sz)

        # Prev + play/pause — fixed control-row spots, fade in with the expand
        ta = _clamp01((t - 0.4) / 0.6)
        _set_centered(self._prev_btn, PREV_CX, h - ROW_CY, CTRL_SZ)
        _set_centered(self._pp_btn,   PP_CX,   h - ROW_CY, PP_SZ)
        self._prev_btn.setAlphaValue_(ta)
        self._pp_btn.setAlphaValue_(ta)

        # Title + artist — under the notch, fade in over the second half of expand
        tw = max(40.0, nright - TEXT_X - 6.0)
        self._title_lbl.setFrame_(NSMakeRect(TEXT_X, h - TITLE_YTOP - 20.0, tw, 20.0))
        self._artist_lbl.setFrame_(NSMakeRect(TEXT_X, h - ARTIST_YTOP - 18.0, tw, 18.0))
        self._title_lbl.setAlphaValue_(ta)
        self._artist_lbl.setAlphaValue_(ta)

        self._bg.setNeedsDisplay_(True)

    # ── Tiny scalar animator (single shared timer) ─────────────────────────────

    def _animate(self, name, target, dur):
        cur = self._t if name == 't' else self._alpha
        self._anim[name] = (cur, target, time.time(), dur)
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                TICK, True, lambda tm: self._tick())

    def _tick(self):
        now  = time.time()
        done = []
        for name, (frm, tgt, start, dur) in list(self._anim.items()):
            p = 1.0 if dur <= 0 else min(1.0, (now - start) / dur)
            val = _lerp(frm, tgt, _ease(p))
            if name == 't':
                self._t = val
            else:
                self._alpha = val
            if p >= 1.0:
                if name == 't':
                    self._t = tgt
                else:
                    self._alpha = tgt
                done.append(name)
        for n in done:
            del self._anim[n]
        self._apply_layout()
        if not self._anim and self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def show(self, title, artist, artwork_url=None):
        def _main():
            self._title_lbl.setStringValue_(title)
            self._artist_lbl.setStringValue_(artist)
            self._art.setContentTintColor_(NSColor.colorWithWhite_alpha_(1.0, 0.5))
            self._art.setImage_(self._placeholder())
            if self._state == HIDDEN:
                self._state = COLLAPSED
                self._t = 0.0
                self._alpha = 0.0
                self._apply_layout()
                self._win.orderFrontRegardless()
                self._animate('alpha', 1.0, ANIM_FADE)
        run_on_main(_main)
        if artwork_url:
            threading.Thread(target=self._fetch_art, args=(artwork_url,), daemon=True).start()

    def peek(self):
        """Slight expand hint while hovering (stays in the COLLAPSED state)."""
        def _main():
            if self._state == COLLAPSED:
                self._animate('t', PEEK_T, ANIM_PEEK)
        run_on_main(_main)

    def unpeek(self):
        """Return from a peek to the mini bar."""
        def _main():
            if self._state == COLLAPSED:
                self._animate('t', 0.0, ANIM_PEEK)
        run_on_main(_main)

    def expand(self):
        def _main():
            if self._state == HIDDEN:
                return
            self._state = EXPANDED
            self._animate('t', 1.0, ANIM_DUR)
        run_on_main(_main)

    def collapse(self):
        def _main():
            if self._state == HIDDEN:
                return
            self._state = COLLAPSED
            self._animate('t', 0.0, ANIM_DUR)
        run_on_main(_main)

    def hide(self):
        def _main():
            if self._state == HIDDEN:
                return
            self._state = HIDDEN
            self._animate('alpha', 0.0, ANIM_FADE)
        run_on_main(_main)

    def update_play_state(self, playing):
        sym = 'pause.fill' if playing else 'play.fill'
        run_on_main(lambda: self._pp_btn.setImage_(
            NSImage.imageWithSystemSymbolName_accessibilityDescription_(sym, None)))

    def is_shown(self):
        return self._state != HIDDEN

    def is_expanded(self):
        return self._state == EXPANDED

    def contains_point(self, loc):
        fr = self._win.frame()
        return (
            fr.origin.x <= loc.x < fr.origin.x + fr.size.width
            and fr.origin.y <= loc.y < fr.origin.y + fr.size.height
        )

    # ── Artwork ────────────────────────────────────────────────────────────────

    def _placeholder(self):
        return NSImage.imageWithSystemSymbolName_accessibilityDescription_('music.note', None)

    def _fetch_art(self, url):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                data = r.read()
            ns_data = NSData.dataWithBytes_length_(data, len(data))
            img = NSImage.alloc().initWithData_(ns_data)
            if img:
                run_on_main(lambda: self._set_art(img))
        except Exception:
            pass

    def _set_art(self, img):
        self._art.setContentTintColor_(None)
        self._art.setImage_(img)

    # ── Control callbacks ──────────────────────────────────────────────────────

    def _prev(self):
        if self._on_prev:
            self._on_prev()

    def _pp(self):
        if self._on_play_pause:
            self._on_play_pause()

    def _next(self):
        if self._on_next:
            self._on_next()


def _set_centered(view, cx, cy, sz):
    view.setFrame_(NSMakeRect(cx - sz / 2.0, cy - sz / 2.0, sz, sz))


def _btn_make(sym, tint, callback, store):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(sym, None)
    b = _OverlayButton.alloc().initWithFrame_(NSMakeRect(0, 0, FWD_SZ, FWD_SZ))
    b.setImage_(img)
    b.setBordered_(False)
    b.setContentTintColor_(tint)
    h = _BtnHandler.alloc().init()
    h.fn = callback
    b.setTarget_(h)
    b.setAction_('act:')
    store.append(h)
    return b
