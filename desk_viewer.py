import json
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, filedialog

try:
    from openai import OpenAI
except Exception:  # Module might be missing; handled later
    OpenAI = None


DATA_PATH = Path(__file__).parent / "data" / "items.json"
API_KEY_PATH = Path(__file__).parent / "apu_key.txt"
CANVAS_WIDTH = 900
CANVAS_HEIGHT = 550
DOT_RADIUS = 10
MODEL_NAME = "gpt-4o-mini"


@dataclass
class DeskItem:
    timestamp: str
    name: str
    x: float
    y: float
    color: str

    @classmethod
    def from_dict(cls, data: dict) -> "DeskItem":
        return cls(
            timestamp=data.get("timestamp") or "",
            name=data.get("name") or "",
            x=float(data.get("x", 0)),
            y=float(data.get("y", 0)),
            color=data.get("color") or "",
        )

    def to_dict(self) -> dict:
        return asdict(self)


class DeskApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Desk Items Viewer")
        self.configure(bg="black")
        self.client = self._init_openai_client()
        self.items: list[DeskItem] = []
        self.last_loaded_mtime: float | None = None

        self.base_family = self._setup_fonts()
        self.ui_font = (self.base_family, 12)

        self._build_ui()
        self._load_initial_items()
        self._start_file_watch()

    def _setup_fonts(self) -> str:
        # Pick a Chinese-friendly font to avoid亂碼/缺字.
        preferred = [
            "Noto Sans CJK TC",
            "Noto Sans TC",
            "Source Han Sans TC",
            "WenQuanYi Micro Hei",
            "Sarasa Gothic TC",
            "Taipei Sans TC Beta",
            "PingFang TC",
            "Microsoft JhengHei",
            "Arial",
            "Helvetica",
        ]
        # X11 core fonts (non-Xft) fallback list for environments where tkfont.families() is limited.
        preferred_xcore = [
            "song ti",
            "fangsong ti",
            "gothic",
            "mincho",
            "clearlyu",
        ]
        available = set(tkfont.families())

        def try_font(name: str) -> str | None:
            if not name:
                return None
            try:
                tkfont.Font(family=name, size=12)
                return name
            except tk.TclError:
                return None

        family = None
        for candidate in preferred:
            if candidate in available and try_font(candidate):
                family = candidate
                break
        if family is None:
            for candidate in preferred_xcore:
                if candidate in available and try_font(candidate):
                    family = candidate
                    break
        if family is None:
            family = tkfont.nametofont("TkDefaultFont").actual("family")

        tkfont.nametofont("TkDefaultFont").config(family=family, size=12)
        tkfont.nametofont("TkTextFont").config(family=family, size=12)
        tkfont.nametofont("TkMenuFont").config(family=family, size=12)
        tkfont.nametofont("TkHeadingFont").config(family=family, size=12)
        return family

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=3)
        control_frame = tk.Frame(self, bg="black", padx=12, pady=12)
        control_frame.grid(row=0, column=0, sticky="nsew")
        self._build_controls(control_frame)

        canvas_frame = tk.Frame(self, bg="black", padx=12, pady=12)
        canvas_frame.grid(row=0, column=1, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            canvas_frame,
            width=CANVAS_WIDTH,
            height=CANVAS_HEIGHT,
            bg="black",
            highlightthickness=0,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self._handle_canvas_click)

        self.status_var = tk.StringVar(value="Click canvas to fill coordinates, or type them.")
        status = tk.Label(
            canvas_frame, textvariable=self.status_var, fg="#bbbbbb", bg="black"
        )
        status.grid(row=1, column=0, sticky="we", pady=(6, 0))

    def _build_controls(self, frame: tk.Frame) -> None:
        label_cfg = {"fg": "#f0f0f0", "bg": "black", "anchor": "w", "font": self.ui_font}
        entry_cfg = {
            "bg": "#161616",
            "fg": "#f5f5f5",
            "insertbackground": "#f5f5f5",
            "bd": 1,
            "highlightthickness": 0,
            "font": self.ui_font,
        }
        button_cfg = {
            "fill": "x",
            "pady": (0, 6),
        }
        button_style = {
            "bg": "#dcdcdc",
            "fg": "#111111",
            "activebackground": "#c0c0c0",
            "activeforeground": "#111111",
            "relief": tk.FLAT,
            "font": self.ui_font,
        }

        tk.Label(frame, text="Data file", **label_cfg).pack(fill="x")
        self.path_var = tk.StringVar(value=str(DATA_PATH))
        tk.Entry(frame, textvariable=self.path_var, **entry_cfg).pack(
            fill="x", pady=(0, 8)
        )
        tk.Button(frame, text="Choose file", command=self._choose_file, **button_style).pack(fill="x", pady=(0, 10))

        tk.Label(frame, text="Placed time (ISO)", **label_cfg).pack(fill="x")
        self.time_var = tk.StringVar(value=datetime.now().isoformat(timespec="seconds"))
        tk.Entry(frame, textvariable=self.time_var, **entry_cfg).pack(fill="x", pady=(0, 6))

        tk.Label(frame, text="Item name", **label_cfg).pack(fill="x")
        self.name_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.name_var, **entry_cfg).pack(fill="x", pady=(0, 6))

        tk.Label(frame, text="X (0-1)", **label_cfg).pack(fill="x")
        self.x_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.x_var, **entry_cfg).pack(fill="x", pady=(0, 6))

        tk.Label(frame, text="Y (0-1)", **label_cfg).pack(fill="x")
        self.y_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.y_var, **entry_cfg).pack(fill="x", pady=(0, 6))

        tk.Label(frame, text="Color", **label_cfg).pack(fill="x")
        self.color_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.color_var, **entry_cfg).pack(fill="x", pady=(0, 10))

        tk.Button(frame, text="Add item", command=self._add_item, **button_style).pack(**button_cfg)
        tk.Button(frame, text="Save", command=self._save_items, **button_style).pack(**button_cfg)
        tk.Button(frame, text="Reload now", command=lambda: self._load_from_path(), **button_style).pack(**button_cfg)

        tk.Label(frame, text="Ask the assistant", **label_cfg).pack(fill="x", pady=(12, 0))
        self.user_question = tk.Text(
            frame,
            height=4,
            bg="#161616",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            wrap="word",
            font=self.ui_font,
            bd=1,
            highlightthickness=0,
        )
        self.user_question.pack(fill="both", expand=False, pady=(0, 10))

        send_frame = tk.Frame(frame, bg="black")
        send_frame.pack(fill="x", pady=(0, 10))
        tk.Button(send_frame, text="Send", command=self._send_question, **button_style).pack(fill="x")

        tk.Label(frame, text="Chat", **label_cfg).pack(fill="x", pady=(8, 0))
        self.response_box = tk.Text(
            frame,
            height=12,
            bg="#0d0d0d",
            fg="#e2e2e2",
            insertbackground="#e2e2e2",
            wrap="word",
            font=self.ui_font,
        )
        self.response_box.pack(fill="both", expand=True)
        self.response_box.insert("1.0", "Assistant is ready. Ask about the desk items.\n\n")
        self.response_box.config(state="disabled")

    def _load_initial_items(self) -> None:
        if DATA_PATH.exists():
            self.path_var.set(str(DATA_PATH))
            self._load_from_path()
        else:
            self.items = []
            self._redraw_canvas()

    def _choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose items file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(DATA_PATH.parent),
        )
        if selected:
            self.path_var.set(selected)
            self._load_from_path()

    def _handle_canvas_click(self, event) -> None:
        x_norm = round(event.x / CANVAS_WIDTH, 3)
        y_norm = round(event.y / CANVAS_HEIGHT, 3)
        self.x_var.set(str(x_norm))
        self.y_var.set(str(y_norm))
        self.status_var.set(f"Selected: x={x_norm}, y={y_norm}")

    def _add_item(self) -> None:
        try:
            item = DeskItem(
                timestamp=self.time_var.get().strip() or datetime.now().isoformat(),
                name=self.name_var.get().strip(),
                x=float(self.x_var.get()),
                y=float(self.y_var.get()),
                color=self.color_var.get().strip(),
            )
        except ValueError:
            messagebox.showerror("Format error", "X and Y must be numbers (0-1).")
            return

        if not item.name:
            messagebox.showerror("Missing name", "Please enter item name.")
            return
        if not (0 <= item.x <= 1 and 0 <= item.y <= 1):
            messagebox.showerror("Coordinate error", "X/Y must be between 0 and 1.")
            return

        self.items.append(item)
        self._redraw_canvas()
        self.status_var.set(f"Added: {item.name}")
        self.name_var.set("")
        self.color_var.set("")

    def _save_items(self) -> None:
        path = Path(self.path_var.get())
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.to_dict() for item in self.items]
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            self.last_loaded_mtime = path.stat().st_mtime
        except OSError:
            self.last_loaded_mtime = None
        self.status_var.set(f"Saved to {path}")

    def _load_from_path(self, show_errors: bool = True, show_status: bool = True) -> None:
        path = Path(self.path_var.get())
        if not path.exists():
            if show_errors:
                messagebox.showerror("File missing", f"Cannot find {path}")
            return
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:  # json errors
            if show_errors:
                messagebox.showerror("Load failed", f"Cannot parse file: {exc}")
            return

        self.items = [DeskItem.from_dict(d) for d in data]
        try:
            self.last_loaded_mtime = path.stat().st_mtime
        except OSError:
            self.last_loaded_mtime = None
        self._redraw_canvas()
        if show_status:
            self.status_var.set(f"Loaded {len(self.items)} items from {path}")

    def _redraw_canvas(self) -> None:
        self.canvas.delete("all")
        # Draw a subtle desk border to help orientation.
        margin = 16
        self.canvas.create_rectangle(
            margin,
            margin,
            CANVAS_WIDTH - margin,
            CANVAS_HEIGHT - margin,
            outline="#3a3a3a",
            width=2,
        )

        for item in self.items:
            cx = item.x * CANVAS_WIDTH
            cy = item.y * CANVAS_HEIGHT
            self.canvas.create_oval(
                cx - DOT_RADIUS,
                cy - DOT_RADIUS,
                cx + DOT_RADIUS,
                cy + DOT_RADIUS,
                fill="white",
                outline="",
            )
            label = f"{item.name} ({item.color})"
            self.canvas.create_text(
                cx,
                cy - DOT_RADIUS - 8,
                text=label,
                fill="#d0d0d0",
                font=(self.base_family, 12),
            )

    def _send_question(self) -> None:
        if self.client is None:
            messagebox.showerror(
                "OpenAI disabled",
                "openai package or OPENAI_API_KEY missing. Please install and set an API key.",
            )
            return
        question = self.user_question.get("1.0", tk.END).strip()
        if not question:
            self.status_var.set("Enter a question first.")
            return
        self._append_chat(f"You: {question}\n")
        self.user_question.delete("1.0", tk.END)

        if not self.items:
            self._append_chat("Assistant: I don't see any items yet.\n\n")
            return

        messages = self._build_messages(question)
        try:
            response = self.client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.2,
            )
        except Exception as exc:
            friendly = self._friendly_error(str(exc))
            self._append_chat(f"Assistant: (error) {friendly}\n\n")
            self.status_var.set("OpenAI call failed")
            return

        content = response.choices[0].message.content.strip()
        self._append_chat(f"Assistant: {content}\n\n")
        self.status_var.set("Response received.")

    def _build_messages(self, question: str):
        item_lines = []
        for idx, item in enumerate(self.items, start=1):
            item_lines.append(
                f"{idx}. {item.name} | color: {item.color or 'unknown'} | pos: ({item.x:.3f}, {item.y:.3f}) | placed: {item.timestamp}"
            )
        items_desc = "\n".join(item_lines) if item_lines else "No items."
        user_content = (
            "Current desk items (coords 0-1, origin top-left):\n"
            f"{items_desc}\n\n"
            f"Question: {question}"
        )
        return [
            {
                "role": "system",
                "content": "You are a concise assistant. Answer only the user's question using the desk items context. Do not add extra commentary.",
            },
            {"role": "user", "content": user_content},
        ]

    def _append_chat(self, text: str) -> None:
        self.response_box.config(state="normal")
        self.response_box.insert(tk.END, text)
        self.response_box.see(tk.END)
        self.response_box.config(state="disabled")

    def _start_file_watch(self) -> None:
        # Poll for file changes to auto-refresh items.
        self.after(1000, self._poll_file_change)

    def _poll_file_change(self) -> None:
        try:
            path = Path(self.path_var.get())
            if path.exists():
                mtime = path.stat().st_mtime
                if self.last_loaded_mtime is None or mtime > self.last_loaded_mtime:
                    self._load_from_path(show_errors=False, show_status=True)
        except Exception:
            # Avoid crashing UI on watch errors.
            pass
        self.after(1000, self._poll_file_change)

    def _friendly_error(self, msg: str) -> str:
        lower = msg.lower()
        if "insufficient_quota" in lower or "quota" in lower:
            return "API quota exceeded or unavailable. Check billing or use another API key."
        if "401" in lower or "invalid_api_key" in lower:
            return "API key invalid or missing. Check apu_key.txt or OPENAI_API_KEY."
        return msg

    def _init_openai_client(self):
        if OpenAI is None:
            return None
        if not os.environ.get("OPENAI_API_KEY"):
            if API_KEY_PATH.exists():
                try:
                    key = API_KEY_PATH.read_text(encoding="utf-8").strip()
                    if key:
                        os.environ["OPENAI_API_KEY"] = key
                except Exception:
                    pass
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        try:
            return OpenAI()
        except Exception:
            return None


def main():
    try:
        app = DeskApp()
        app.mainloop()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
