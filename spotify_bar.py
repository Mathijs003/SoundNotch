#!/usr/bin/env python3
import rumps
import subprocess
import time as _time
import json
import os
from AppKit import NSEvent
from notch_window import NotchWindow, _in_notch_zone

MAX_TITLE_LEN       = 45
AUTO_COLLAPSE_SECS  = 3.0   # new-song → auto-collapse after this
HOVER_COLLAPSE_SECS = 1.0   # mouse leaves widget → collapse after this
HOVER_EXPAND_DELAY  = 0.5   # dwell time hovering before full expand (anti-accidental)
HOVER_POLL          = 0.1

CONFIG_DIR  = os.path.expanduser("~/Library/Application Support/SoundNotch")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f)
    except Exception:
        pass


def _as(script):
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""


def get_track_id():
    return _as('''
tell application "System Events"
    if not (exists process "Spotify") then return ""
end tell
tell application "Spotify"
    if player state is playing then return id of current track
    return ""
end tell
''')


def get_track_details():
    raw = _as('''
tell application "Spotify"
    set t to current track
    try
        set art to artwork url of t
    on error
        set art to ""
    end try
    return (name of t) & "|||" & (artist of t) & "|||" & art
end tell
''')
    parts = raw.split("|||", 2) if "|||" in raw else [raw, "", ""]
    return parts[0], (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")


def get_playback():
    raw = _as('''
tell application "Spotify"
    if player state is playing then
        set st to "playing"
    else if player state is paused then
        set st to "paused"
    else
        return ""
    end if
    set pos to player position
    set dur to duration of current track
    return (pos as text) & "|||" & (dur as text) & "|||" & st
end tell
''')
    if "|||" not in raw:
        return None
    parts = raw.split("|||", 2)
    try:
        pos     = float(parts[0])
        dur_ms  = float(parts[1])
        playing = parts[2].strip() == "playing"
        return pos, dur_ms / 1000.0, playing
    except Exception:
        return None


def get_volume():
    raw = _as('tell application "Spotify" to get sound volume')
    try:
        return int(float(raw))
    except Exception:
        return None


def set_volume(v):
    v = max(0, min(100, int(v)))
    _as(f'tell application "Spotify" to set sound volume to {v}')


def truncate(t, n):
    return t if len(t) <= n else t[:n - 1] + "…"


class SoundNotch(rumps.App):
    def __init__(self):
        super().__init__("♪", quit_button=None)

        self._notch = NotchWindow(
            on_prev=self._prev,
            on_play_pause=self._play_pause,
            on_next=self._next,
            on_vol_down=self._vol_down,
            on_vol_up=self._vol_up,
            on_seek=self._seek,
        )
        self._last_id        = None
        self._collapse_at    = None   # timestamp at which to auto-collapse (None = no pending)
        self._hover_since    = None   # timestamp hover (peek) started, or None
        self._last_title     = ""
        self._last_artist    = ""
        self._artwork_url    = ""
        self._last_dur       = 0.0

        cfg = load_config()
        self._show_notch = bool(cfg.get("show_notch", True))
        self._show_title = bool(cfg.get("show_title", True))

        self._track_item = rumps.MenuItem("Spotify not running")
        self._track_item.set_callback(None)

        self._notch_toggle = rumps.MenuItem("Show notch widget", callback=self._toggle_show_notch)
        self._notch_toggle.state = 1 if self._show_notch else 0
        self._title_toggle = rumps.MenuItem("Show name in menu bar", callback=self._toggle_show_title)
        self._title_toggle.state = 1 if self._show_title else 0

        self.menu = [
            self._track_item,
            None,
            rumps.MenuItem("⏮  Previous",    callback=lambda _: self._prev()),
            rumps.MenuItem("⏯  Play / Pause", callback=lambda _: self._play_pause()),
            rumps.MenuItem("⏭  Next",         callback=lambda _: self._next()),
            None,
            self._notch_toggle,
            self._title_toggle,
            None,
            rumps.MenuItem("Quit",            callback=rumps.quit_application),
        ]

        rumps.Timer(self._poll,        2.0).start()
        rumps.Timer(self._check_hover, HOVER_POLL).start()

        t = rumps.Timer(self._first_poll, 1.0)
        t.start()

    # ── Timers ─────────────────────────────────────────────────────────────────

    def _first_poll(self, timer):
        timer.stop()
        self._poll(None)

    def _poll(self, _):
        tid = get_track_id()
        if not tid:
            self.title = "♪"
            self._track_item.title = "Spotify not running"
            self._last_id  = None
            self._last_dur = 0.0
            self._notch.hide()
            return

        new_song = tid != self._last_id
        if new_song:
            self._last_id = tid
            title, artist, art = get_track_details()
            self._last_title  = title
            self._last_artist = artist
            self._artwork_url = art

        if self._show_notch:
            if new_song:
                self._notch.show(self._last_title, self._last_artist, self._artwork_url or None)
                self._notch.expand()
                self._notch.update_play_state(True)
                self._collapse_at = _time.time() + AUTO_COLLAPSE_SECS
            elif not self._notch.is_shown():
                # setting just re-enabled mid-song → bring back the mini bar
                self._notch.show(self._last_title, self._last_artist, self._artwork_url or None)
        else:
            self._notch.hide()

        display = f"{self._last_artist} – {self._last_title}"
        self._track_item.title = display
        self.title = ("♪ " + truncate(display, MAX_TITLE_LEN)) if self._show_title else "♪"

    def _check_hover(self, _):
        """Runs every HOVER_POLL seconds. Handles hover-to-expand and the
        deadline-based auto-collapse (rumps.Timer can't do a one-shot delay —
        it fires immediately and repeats — so we track a target timestamp here)."""
        if not self._notch.is_shown():
            return
        loc = NSEvent.mouseLocation()
        now = _time.time()

        hovering = self._notch.contains_point(loc) or _in_notch_zone(loc)

        if self._notch.is_expanded():
            if hovering:
                # Keep open while hovering; collapse shortly after the mouse leaves.
                self._collapse_at = now + HOVER_COLLAPSE_SECS
            elif self._collapse_at is not None and now >= self._collapse_at:
                self._collapse_at = None
                self._notch.collapse()
        else:
            # Collapsed mini-bar: peek on hover, then fully expand only after a
            # short dwell so quick mouse passes don't accidentally open it.
            if hovering:
                if self._hover_since is None:
                    self._hover_since = now
                    self._notch.peek()                     # slight expand hint
                elif now - self._hover_since >= HOVER_EXPAND_DELAY:
                    self._hover_since = None
                    self._notch.expand()                   # full expand after dwell
                    self._notch.update_play_state(True)
            elif self._hover_since is not None:
                self._hover_since = None
                self._notch.unpeek()                       # mouse left → back to mini

    # ── Controls ────────────────────────────────────────────────────────────────

    def _toggle_show_notch(self, sender):
        sender.state = not sender.state
        self._show_notch = bool(sender.state)
        self._save_settings()
        self._poll(None)        # apply immediately

    def _toggle_show_title(self, sender):
        sender.state = not sender.state
        self._show_title = bool(sender.state)
        self._save_settings()
        self._poll(None)        # apply immediately

    def _save_settings(self):
        save_config({"show_notch": self._show_notch, "show_title": self._show_title})

    def _play_pause(self):
        _as('tell application "Spotify" to playpause')
        pb = get_playback()
        if pb:
            self._notch.update_play_state(pb[2])

    def _next(self):
        _as('tell application "Spotify" to next track')

    def _prev(self):
        _as('tell application "Spotify" to previous track')

    def _vol_down(self):
        v = get_volume()
        if v is not None:
            set_volume(v - 10)

    def _vol_up(self):
        v = get_volume()
        if v is not None:
            set_volume(v + 10)

    def _seek(self, ratio):
        dur = self._last_dur
        if dur > 0:
            pos = ratio * dur
            _as(f'tell application "Spotify" to set player position to {pos}')


if __name__ == "__main__":
    SoundNotch().run()
