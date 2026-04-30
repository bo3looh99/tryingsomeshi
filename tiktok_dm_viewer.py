"""
TikTok DM Viewer  ·  modern bubble UI  ·  circular avatars
Requires: pip install customtkinter Pillow
"""

import glob
import io
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Palette ───────────────────────────────────────────────────────────────────
C_APP      = "#080813"
C_SIDEBAR  = "#0c0c1e"
C_CARD     = "#11112a"
C_HOVER    = "#17173a"
C_SEL      = "#1c1c42"
C_HEADER   = "#0e0e26"
C_BUBBLE_ME    = "#1a4db5"
C_BUBBLE_THEM  = "#151532"
C_ACCENT   = "#3b7eff"
C_ACCENT2  = "#5565a8"
C_TEXT     = "#eeeeff"
C_DIM      = "#5a5a90"
C_TS       = "#35355a"
C_DIVIDER  = "#12122e"
C_WHITE    = "#ffffff"

# ── Fonts ─────────────────────────────────────────────────────────────────────
# Pick first font family available on the system
def _pick_font():
    import tkinter.font as tkfont
    root = tk.Tk(); root.withdraw()
    avail = set(tkfont.families(root)); root.destroy()
    for name in ("Inter", "Ubuntu", "Cantarell", "Segoe UI",
                 "Helvetica Neue", "DejaVu Sans"):
        if name in avail:
            return name
    return "TkDefaultFont"

FONT = _pick_font()

def F(size, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


# ── Avatar helpers ────────────────────────────────────────────────────────────

def _circle_crop(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    return result


def _placeholder_avatar(size: int, letter: str = "?",
                         bg: str = "#2a2a5a", fg: str = "#8888cc") -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=bg)
    # Simple cross/initial — PIL has no font load guarantee, skip text
    return img


def load_avatar(path: str | None, size: int) -> ctk.CTkImage | None:
    if not path or not os.path.exists(path):
        return None
    try:
        img = Image.open(path)
        img = _circle_crop(img, size * 2)  # 2× for HiDPI
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return None


def placeholder_avatar(size: int, letter: str = "?") -> ctk.CTkImage:
    img = _placeholder_avatar(size * 2, letter)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


# ── Data helpers ──────────────────────────────────────────────────────────────

def find_latest_json():
    files = glob.glob("tiktok_dms_full_*.json")
    return max(files, key=os.path.getmtime) if files else None


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {}, data
    return data.get("owner_profile", {}), data.get("conversations", [])


# ── ConvItem widget ───────────────────────────────────────────────────────────

class ConvItem(ctk.CTkFrame):
    def __init__(self, parent, username: str, msg_count: int,
                 avatar_path: str | None, on_click, **kw):
        super().__init__(parent, fg_color=C_CARD, corner_radius=10,
                         cursor="hand2", **kw)
        self._on_click = on_click
        self._selected = False

        av_img = load_avatar(avatar_path, 40) or placeholder_avatar(40, username[:1].upper())

        av_lbl = ctk.CTkLabel(self, image=av_img, text="", width=40, height=40)
        av_lbl.pack(side="left", padx=(10, 8), pady=8)

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, pady=8, padx=(0, 10))

        ctk.CTkLabel(text_frame, text=username, font=F(12, "bold"),
                     text_color=C_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(text_frame, text=f"{msg_count} messages",
                     font=F(10), text_color=C_DIM, anchor="w").pack(anchor="w")

        # bind clicks on all sub-widgets
        for w in (self, av_lbl, text_frame) + tuple(text_frame.winfo_children()):
            w.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._hover_in)
        self.bind("<Leave>", self._hover_out)

    def _click(self, _=None):
        self._on_click()

    def _hover_in(self, _=None):
        if not self._selected:
            self.configure(fg_color=C_HOVER)

    def _hover_out(self, _=None):
        if not self._selected:
            self.configure(fg_color=C_CARD)

    def set_selected(self, selected: bool):
        self._selected = selected
        self.configure(fg_color=C_SEL if selected else C_CARD)


# ── Main app ──────────────────────────────────────────────────────────────────

class TikTokViewer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TikTok DM Viewer")
        self.geometry("1200x760")
        self.minsize(820, 540)
        self.configure(fg_color=C_APP)

        self._owner: dict = {}
        self._convos: list = []
        self._file: str | None = None
        self._conv_items: list[ConvItem] = []
        self._selected: int | None = None
        self._owner_avatar: ctk.CTkImage | None = None

        self._build_ui()
        self._add_menu()

        latest = find_latest_json()
        if latest:
            self._load_file(latest)
        else:
            self._set_status("No tiktok_dms_full_*.json found — File > Open")

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _add_menu(self):
        mb = tk.Menu(self, bg=C_CARD, fg=C_TEXT,
                     activebackground=C_ACCENT2, activeforeground=C_WHITE,
                     relief="flat", borderwidth=0)
        fm = tk.Menu(mb, tearoff=0, bg=C_CARD, fg=C_TEXT,
                     activebackground=C_ACCENT2, activeforeground=C_WHITE)
        fm.add_command(label="  Open JSON…   Ctrl+O", command=self._open_dialog)
        fm.add_separator()
        fm.add_command(label="  Exit", command=self.destroy)
        mb.add_cascade(label="  File  ", menu=fm)
        self.configure(menu=mb)
        self.bind("<Control-o>", lambda _: self._open_dialog())

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Sidebar ───────────────────────────────────────────────────────────
        self._sidebar = ctk.CTkFrame(self, fg_color=C_SIDEBAR,
                                     corner_radius=0, width=280)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # Owner card
        self._owner_card = ctk.CTkFrame(self._sidebar, fg_color=C_CARD,
                                         corner_radius=14)
        self._owner_card.pack(fill="x", padx=12, pady=(14, 8))

        self._oc_avatar_lbl = ctk.CTkLabel(self._owner_card, text="",
                                            width=48, height=48)
        self._oc_avatar_lbl.pack(side="left", padx=(12, 8), pady=12)

        oc_info = ctk.CTkFrame(self._owner_card, fg_color="transparent")
        oc_info.pack(side="left", fill="both", expand=True, pady=10, padx=(0, 10))

        self._oc_username = ctk.CTkLabel(oc_info, text="—", font=F(13, "bold"),
                                          text_color=C_ACCENT, anchor="w")
        self._oc_username.pack(anchor="w")
        self._oc_stats = ctk.CTkLabel(oc_info, text="", font=F(10),
                                       text_color=C_DIM, anchor="w", wraplength=160)
        self._oc_stats.pack(anchor="w")

        # Search
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        ctk.CTkEntry(self._sidebar,
                     placeholder_text="🔍  Search conversations…",
                     textvariable=self._search_var,
                     fg_color=C_CARD, border_color=C_DIVIDER, border_width=1,
                     text_color=C_TEXT, placeholder_text_color=C_DIM,
                     font=F(12), height=36, corner_radius=10,
                     ).pack(fill="x", padx=12, pady=(0, 6))

        # Conversation list
        self._conv_scroll = ctk.CTkScrollableFrame(
            self._sidebar, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=C_HOVER,
            scrollbar_button_hover_color=C_ACCENT2,
        )
        self._conv_scroll.pack(fill="both", expand=True, padx=0)

        # ── Vertical divider ─────────────────────────────────────────────────
        ctk.CTkFrame(self, fg_color=C_DIVIDER, width=1,
                     corner_radius=0).pack(side="left", fill="y")

        # ── Thread panel ─────────────────────────────────────────────────────
        self._thread_panel = ctk.CTkFrame(self, fg_color=C_APP, corner_radius=0)
        self._thread_panel.pack(side="left", fill="both", expand=True)

        # Header
        self._th_header = ctk.CTkFrame(self._thread_panel, fg_color=C_HEADER,
                                        corner_radius=0, height=62)
        self._th_header.pack(fill="x")
        self._th_header.pack_propagate(False)

        self._th_av = ctk.CTkLabel(self._th_header, text="", width=40, height=40)
        self._th_av.pack(side="left", padx=(16, 8), pady=10)

        th_info = ctk.CTkFrame(self._th_header, fg_color="transparent")
        th_info.pack(side="left", fill="y", pady=10)

        self._th_name = ctk.CTkLabel(th_info, text="", font=F(14, "bold"),
                                      text_color=C_TEXT, anchor="w")
        self._th_name.pack(anchor="w")
        self._th_sub = ctk.CTkLabel(th_info, text="", font=F(10),
                                     text_color=C_DIM, anchor="w")
        self._th_sub.pack(anchor="w")

        ctk.CTkFrame(self._thread_panel, fg_color=C_DIVIDER,
                     height=1, corner_radius=0).pack(fill="x")

        # Message scroll area
        self._msg_scroll = ctk.CTkScrollableFrame(
            self._thread_panel, fg_color=C_APP, corner_radius=0,
            scrollbar_button_color=C_HOVER,
            scrollbar_button_hover_color=C_ACCENT2,
        )
        self._msg_scroll.pack(fill="both", expand=True, padx=0)

        # Placeholder label (shown when no convo selected)
        self._placeholder = ctk.CTkLabel(
            self._msg_scroll,
            text="← Select a conversation",
            font=F(15), text_color=C_DIM,
        )
        self._placeholder.pack(expand=True, pady=120)

        # ── Status bar ────────────────────────────────────────────────────────
        ctk.CTkFrame(self, fg_color=C_DIVIDER, height=1,
                     corner_radius=0).pack(side="bottom", fill="x")
        self._status_var = tk.StringVar(value="Ready")
        ctk.CTkLabel(self, textvariable=self._status_var,
                     font=F(9), text_color=C_DIM, fg_color=C_SIDEBAR,
                     anchor="w", padx=14,
                     ).pack(side="bottom", fill="x", ipady=5)

    # ── File loading ──────────────────────────────────────────────────────────

    def _open_dialog(self):
        path = filedialog.askopenfilename(
            title="Open TikTok DM JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        try:
            owner, convos = load_json(path)
        except Exception as e:
            messagebox.showerror("Load error", f"Could not read file:\n{e}")
            return
        self._owner = owner
        self._convos = convos
        self._file = path
        self._refresh_owner_card()
        self._rebuild_conv_list(convos)
        self._clear_thread()
        fname = os.path.basename(path)
        self._set_status(
            f"{fname}  ·  {len(convos)} conversation{'s' if len(convos) != 1 else ''}"
        )

    # ── Owner card ────────────────────────────────────────────────────────────

    def _refresh_owner_card(self):
        p = self._owner
        self._owner_avatar = load_avatar(p.get("avatar_path"), 48) or placeholder_avatar(48, "?")
        self._oc_avatar_lbl.configure(image=self._owner_avatar)
        self._oc_username.configure(
            text=f"@{p.get('username', '—')}" if p.get("username") else "—"
        )
        parts = []
        if p.get("followers_count"):
            parts.append(f"👥 {p['followers_count']}")
        if p.get("region"):
            parts.append(f"🌍 {p['region']}")
        if p.get("store_region"):
            parts.append(f"🏪 {p['store_region']}")
        self._oc_stats.configure(text="  ".join(parts) if parts else "")

    # ── Conversation list ─────────────────────────────────────────────────────

    def _rebuild_conv_list(self, convos: list):
        for item in self._conv_items:
            item.destroy()
        self._conv_items.clear()
        self._selected = None

        for i, convo in enumerate(convos):
            name  = convo.get("username", f"conv_{i+1}")
            count = convo.get("message_count", len(convo.get("messages", [])))
            av    = convo.get("avatar_path")
            item  = ConvItem(
                self._conv_scroll, name, count, av,
                on_click=lambda idx=i: self._select_conv(idx),
            )
            item.pack(fill="x", padx=8, pady=3)
            self._conv_items.append(item)

    def _on_search(self, *_):
        q = self._search_var.get().lower()
        for i, item in enumerate(self._conv_items):
            name = self._convos[i].get("username", "").lower()
            if q in name:
                item.pack(fill="x", padx=8, pady=3)
            else:
                item.pack_forget()

    def _select_conv(self, idx: int):
        for i, item in enumerate(self._conv_items):
            item.set_selected(i == idx)
        self._selected = idx
        self._load_thread(idx)

    # ── Message thread ────────────────────────────────────────────────────────

    def _clear_thread(self):
        for w in self._msg_scroll.winfo_children():
            w.destroy()
        self._placeholder = ctk.CTkLabel(
            self._msg_scroll,
            text="← Select a conversation",
            font=F(15), text_color=C_DIM,
        )
        self._placeholder.pack(expand=True, pady=120)
        self._th_name.configure(text="")
        self._th_sub.configure(text="")
        self._th_av.configure(image=None, text="")

    def _load_thread(self, idx: int):
        convo    = self._convos[idx]
        username = convo.get("username", "Unknown")
        messages = convo.get("messages", [])
        av_path  = convo.get("avatar_path")

        # Header
        th_img = load_avatar(av_path, 40) or placeholder_avatar(40, username[:1].upper())
        self._th_av.configure(image=th_img)
        self._th_name.configure(text=username)
        self._th_sub.configure(text=f"{len(messages)} messages")

        # Clear
        for w in self._msg_scroll.winfo_children():
            w.destroy()

        if not messages:
            ctk.CTkLabel(
                self._msg_scroll,
                text="No messages loaded for this conversation.",
                font=F(13), text_color=C_DIM,
            ).pack(pady=80)
            return

        # Cached "them" avatar (32px) for inline use
        them_av = load_avatar(av_path, 32) or placeholder_avatar(32, username[:1].upper())

        prev_is_me = None
        for msg in messages:
            is_me = msg.get("is_me", False)
            text  = msg.get("text", "").strip() or "[media / attachment]"
            ts    = msg.get("timestamp", "")
            if ts == "—":
                ts = ""

            # Gap between sender groups
            if prev_is_me is not None and prev_is_me != is_me:
                ctk.CTkFrame(self._msg_scroll, fg_color="transparent",
                             height=6).pack()
            prev_is_me = is_me

            self._add_bubble(username, text, ts, is_me, them_av)

        # Scroll to bottom
        self.update_idletasks()
        try:
            self._msg_scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _add_bubble(self, username: str, text: str, ts: str,
                    is_me: bool, them_av: ctk.CTkImage):
        # Outer row — full width
        row = ctk.CTkFrame(self._msg_scroll, fg_color="transparent")
        row.pack(fill="x", padx=0, pady=1)

        if is_me:
            # Right side: spacer + bubble
            ctk.CTkFrame(row, fg_color="transparent").pack(
                side="left", expand=True, fill="x")

            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="right", padx=(0, 14), pady=2)

            bubble = ctk.CTkFrame(col, fg_color=C_BUBBLE_ME, corner_radius=18)
            bubble.pack(anchor="e")
            ctk.CTkLabel(
                bubble, text=text,
                font=F(13), text_color=C_WHITE,
                wraplength=310, justify="left",
                padx=14, pady=10,
            ).pack()

            if ts:
                ctk.CTkLabel(col, text=ts, font=F(9),
                             text_color=C_TS, anchor="e").pack(anchor="e", padx=4)

        else:
            # Left side: avatar + bubble
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", padx=(14, 0), pady=2)

            inner = ctk.CTkFrame(col, fg_color="transparent")
            inner.pack(anchor="w")

            av_lbl = ctk.CTkLabel(inner, image=them_av, text="",
                                   width=32, height=32)
            av_lbl.pack(side="left", anchor="s", padx=(0, 6))

            bubble = ctk.CTkFrame(inner, fg_color=C_BUBBLE_THEM,
                                   corner_radius=18)
            bubble.pack(side="left")
            ctk.CTkLabel(
                bubble, text=text,
                font=F(13), text_color=C_TEXT,
                wraplength=310, justify="left",
                padx=14, pady=10,
            ).pack()

            if ts:
                ctk.CTkLabel(col, text=ts, font=F(9),
                             text_color=C_TS, anchor="w").pack(anchor="w", padx=40)

            # Spacer on right
            ctk.CTkFrame(row, fg_color="transparent").pack(
                side="right", expand=True, fill="x")

    # ── Status ────────────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status_var.set(text)


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TikTokViewer()
    app.mainloop()
