from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False
    DND_FILES = None
    TkinterDnD = None


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "gui_config.txt"
DATA_ROOT = ROOT / "data"
PROJECTS_ROOT = DATA_ROOT / "projects"
DEFAULT_PROJECT_ID = "default"

DEFAULT_CONFIG = {
    "title": "Local LLM GUI",
    "width": "1200",
    "height": "820",
    "ollama_host": "127.0.0.1",
    "ollama_port": "11434",
    "setup_complete": "false",
    "bridge_host": "127.0.0.1",
    "bridge_port": "18767",
    "default_model": "qwen3:8b",
    "font_family": "Yu Gothic UI",
    "font_size": "11",
    "theme": "dark",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".java", ".rs",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".csv",
    ".log", ".sh", ".cmd", ".bat", ".c", ".cpp", ".h", ".hpp",
}
MAX_TEXT_ATTACHMENT = 2 * 1024 * 1024
MAX_IMAGE_ATTACHMENT = 10 * 1024 * 1024


def load_config() -> dict[str, str]:
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.is_file():
        for raw in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in config:
                config[key] = value.strip()
    if "://" in config.get("ollama_host", ""):
        from urllib.parse import urlparse
        parsed = urlparse(config["ollama_host"])
        if parsed.hostname:
            config["ollama_host"] = parsed.hostname
        if parsed.port:
            config["ollama_port"] = str(parsed.port)
    return config


def save_config(config: dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    order = [
        "title", "width", "height", "ollama_host", "ollama_port",
        "bridge_host", "bridge_port", "default_model", "font_family",
        "font_size", "theme", "setup_complete"
    ]
    lines = [f"{key}={config[key]}" for key in order if key in config]
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ollama_url(config: dict[str, str]) -> str:
    host = config.get("ollama_host", "127.0.0.1").strip()
    port = config.get("ollama_port", "11434").strip()
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return f"http://{host}:{port}"


CONFIG = load_config()


def safe_filename(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE)
    return value[:80] or "conversation"


def project_filename(value: str) -> str:
    return safe_filename(value.lower().replace(" ", "_"))


def request_json(url: str, payload: dict | None = None, timeout: float = 30.0):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


MOJIBAKE_MARKERS = (
    "縺", "繧", "譁", "莨", "蜿", "逡", "譟", "荳", "帙", "�"
)

def looks_like_mojibake(text: str) -> bool:
    if not text:
        return False
    marker_count = sum(text.count(marker) for marker in MOJIBAKE_MARKERS)
    return marker_count >= 2 and marker_count / max(1, len(text)) >= 0.015


def repair_mojibake(text: str) -> str:
    """Repair common UTF-8 -> CP932 mojibake when it is strongly indicated."""
    if not looks_like_mojibake(text):
        return text
    try:
        repaired = text.encode("cp932").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if repaired and not looks_like_mojibake(repaired):
        return repaired
    return text


def decode_text_bytes(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "UTF-8 BOM"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "UTF-16"
        except UnicodeDecodeError:
            pass

    candidates = (
        ("UTF-8", "utf-8"),
        ("CP932", "cp932"),
        ("EUC-JP", "euc_jp"),
        ("ISO-2022-JP", "iso2022_jp"),
    )
    decoded = []
    for name, encoding in candidates:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        decoded.append((name, text))

    if not decoded:
        return data.decode("utf-8", errors="replace"), "UTF-8 (置換)"

    for name, text in decoded:
        if looks_like_mojibake(text):
            repaired = repair_mojibake(text)
            if repaired != text:
                return repaired, f"{name} → UTF-8復元"

    for name, text in decoded:
        if any("\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" for c in text):
            return text, name

    return decoded[0]


@dataclass
class Message:
    role: str
    content: str
    attachments: list[dict] = field(default_factory=list)

    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
            "attachments": self.attachments,
        }

    @classmethod
    def from_dict(cls, data):
        attachments = data.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        return cls(
            role=str(data.get("role", "user")),
            content=str(data.get("content", "")),
            attachments=[a for a in attachments if isinstance(a, dict)],
        )


@dataclass
class Conversation:
    conversation_id: str
    title: str = "新しい会話"
    messages: list[Message] = field(default_factory=list)
    model: str = ""

    def to_dict(self):
        return {
            "id": self.conversation_id,
            "title": self.title,
            "model": self.model,
            "messages": [message.to_dict() for message in self.messages],
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            conversation_id=str(data.get("id", "")),
            title=str(data.get("title", "新しい会話")),
            model=str(data.get("model", "")),
            messages=[
                Message.from_dict(item)
                for item in data.get("messages", [])
                if isinstance(item, dict)
            ],
        )


@dataclass
class Project:
    project_id: str
    name: str
    description: str = ""


class LocalLLMGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = CONFIG
        self.bridge_base = f"http://{self.config['bridge_host']}:{self.config['bridge_port']}"
        self.font_family = self.config["font_family"]
        try:
            self.font_size = int(self.config["font_size"])
        except ValueError:
            self.font_size = 11

        self.generating = False
        self.stop_requested = False
        self.stream_thread: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()
        self.models: list[str] = []
        self.projects: dict[str, Project] = {}
        self.current_project_id = DEFAULT_PROJECT_ID
        self.conversations: dict[str, Conversation] = {}
        self.current_id: str | None = None
        self.attachments: list[Path] = []
        self.code_widgets: list[tuple[tk.Widget, str]] = []
        self.last_status = "待機中"

        self.setup_window()
        self.build_ui()
        self.load_projects()
        self.select_project(DEFAULT_PROJECT_ID)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.process_events)
        self.root.after(300, self.refresh_models)


    def setup_window(self):
        self.root.title(self.config["title"])
        try:
            width = int(float(self.config["width"]))
            height = int(float(self.config["height"]))
        except ValueError:
            width, height = 1200, 820
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(820, 600)
        self.apply_theme()

    def apply_theme(self):
        self.colors = {
            "bg": "#14171b",
            "sidebar": "#1a1e23",
            "sidebar2": "#20252c",
            "panel": "#20242a",
            "panel2": "#252a31",
            "input": "#1b1f24",
            "text": "#eef1f4",
            "muted": "#9da6b0",
            "accent": "#5b8def",
            "accent_hover": "#709dff",
            "danger": "#9d454c",
            "code": "#111419",
            "code_text": "#e9edf2",
            "inline": "#303640",
            "border": "#343a43",
        }
        self.root.configure(bg=self.colors["bg"])
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.style.configure(
            "TButton",
            font=(self.font_family, self.font_size),
            padding=(10, 6),
            background=self.colors["panel2"],
            foreground=self.colors["text"],
            borderwidth=0,
        )
        self.style.map("TButton", background=[("active", self.colors["accent"])])
        self.style.configure(
            "TCombobox",
            font=(self.font_family, self.font_size),
            fieldbackground=self.colors["input"],
            background=self.colors["panel2"],
            foreground=self.colors["text"],
            borderwidth=0,
        )
        self.style.configure("TScrollbar", background=self.colors["panel2"], troughcolor=self.colors["bg"])

    def make_button(self, parent, text, command, *, danger=False, small=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.colors["danger"] if danger else self.colors["panel2"],
            fg=self.colors["text"],
            activebackground=self.colors["accent_hover"],
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=10 if not small else 7,
            pady=7 if not small else 5,
            font=(self.font_family, self.font_size if not small else max(9, self.font_size - 1)),
            cursor="hand2",
        )


    def build_ui(self):
        self.build_menu()

        self.outer = tk.Frame(self.root, bg=self.colors["bg"])
        self.outer.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(self.outer, bg=self.colors["sidebar"], width=270)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.main = tk.Frame(self.outer, bg=self.colors["bg"])
        self.main.pack(side="left", fill="both", expand=True)

        sidebar = self.sidebar
        main = self.main

        title = tk.Label(
            sidebar,
            text="Local LLM GUI",
            bg=self.colors["sidebar"],
            fg=self.colors["text"],
            font=(self.font_family, 15, "bold"),
            anchor="w",
        )
        title.pack(fill="x", padx=14, pady=(14, 10))

        project_box = tk.Frame(sidebar, bg=self.colors["sidebar2"], bd=0, highlightthickness=1, highlightbackground=self.colors["border"])
        project_box.pack(fill="x", padx=10, pady=(0, 9))
        tk.Label(project_box, text="現在のプロジェクト", bg=self.colors["sidebar2"], fg=self.colors["muted"], font=(self.font_family, 8)).pack(anchor="w", padx=11, pady=(8, 0))
        self.project_var = tk.StringVar()
        self.project_current_label = tk.Label(project_box, text="", bg=self.colors["sidebar2"], fg=self.colors["text"], font=(self.font_family, 12, "bold"), anchor="w")
        self.project_current_label.pack(fill="x", padx=11, pady=(1, 6))
        project_controls = tk.Frame(project_box, bg=self.colors["sidebar2"])
        project_controls.pack(fill="x", padx=8, pady=(0, 8))
        self.make_button(project_controls, "プロジェクトを選択", self.choose_project, small=True).pack(side="left", fill="x", expand=True)
        self.make_button(project_controls, "+", self.create_project, small=True).pack(side="left", padx=(5, 0))

        self.make_button(sidebar, "+ 新しい会話", self.new_conversation).pack(fill="x", padx=10, pady=(0, 8))

        list_frame = tk.Frame(sidebar, bg=self.colors["sidebar"])
        list_frame.pack(fill="both", expand=True, padx=8)
        self.conversation_list = tk.Listbox(
            list_frame,
            bg=self.colors["sidebar"],
            fg=self.colors["text"],
            selectbackground=self.colors["accent"],
            selectforeground="white",
            activestyle="none",
            relief="flat",
            highlightthickness=0,
            font=(self.font_family, self.font_size),
        )
        self.conversation_list.pack(side="left", fill="both", expand=True)
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.conversation_list.yview)
        list_scroll.pack(side="right", fill="y")
        self.conversation_list.configure(yscrollcommand=list_scroll.set)
        self.conversation_list.bind("<<ListboxSelect>>", self.on_conversation_selected)
        self.conversation_list.bind("<Double-Button-1>", lambda _e: self.rename_conversation())

        side_actions = tk.Frame(sidebar, bg=self.colors["sidebar"])
        side_actions.pack(fill="x", padx=10, pady=10)
        self.make_button(side_actions, "名前変更", self.rename_conversation, small=True).pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.make_button(side_actions, "削除", self.delete_conversation, danger=True, small=True).pack(side="left", fill="x", expand=True, padx=(3, 0))

        header = tk.Frame(main, bg=self.colors["panel"], height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="モデル", bg=self.colors["panel"], fg=self.colors["muted"], font=(self.font_family, self.font_size, "bold")).pack(side="left", padx=(14, 7))
        self.model_var = tk.StringVar(value=self.config["default_model"])
        self.model_combo = ttk.Combobox(header, textvariable=self.model_var, state="normal")
        self.model_combo.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=11)
        self.make_button(header, "＋モデル", self.install_model, small=True).pack(side="left", padx=(0, 8))
        self.status_var = tk.StringVar(value="Ollama接続確認中...")
        tk.Label(header, textvariable=self.status_var, bg=self.colors["panel"], fg=self.colors["muted"], font=(self.font_family, 9)).pack(side="right", padx=14)

        chat_frame = tk.Frame(main, bg=self.colors["bg"])
        chat_frame.pack(fill="both", expand=True)
        self.chat = tk.Text(
            chat_frame,
            wrap="word",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=26,
            pady=20,
            font=(self.font_family, self.font_size),
            cursor="arrow",
        )
        self.chat.pack(side="left", fill="both", expand=True)
        chat_scroll = ttk.Scrollbar(chat_frame, orient="vertical", command=self.chat.yview)
        chat_scroll.pack(side="right", fill="y")
        self.chat.configure(yscrollcommand=chat_scroll.set, state="disabled")
        self.setup_chat_tags()

        attachment_bar = tk.Frame(main, bg=self.colors["panel"], height=38)
        attachment_bar.pack(fill="x")
        attachment_bar.pack_propagate(False)
        tk.Label(attachment_bar, text="添付", bg=self.colors["panel"], fg=self.colors["muted"], font=(self.font_family, 9)).pack(side="left", padx=(14, 6))
        self.attachment_label = tk.Label(attachment_bar, text="なし", bg=self.colors["panel"], fg=self.colors["muted"], anchor="w", font=(self.font_family, 9))
        self.attachment_label.pack(side="left", fill="x", expand=True)
        self.make_button(attachment_bar, "ファイル追加", self.add_attachments, small=True).pack(side="right", padx=(4, 8), pady=3)
        self.make_button(attachment_bar, "クリア", self.clear_attachments, small=True).pack(side="right", padx=(0, 4), pady=3)

        input_outer = tk.Frame(main, bg=self.colors["panel"], height=116)
        input_outer.pack(fill="x")
        input_outer.pack_propagate(False)
        self.input = tk.Text(
            input_outer,
            height=4,
            wrap="word",
            bg=self.colors["input"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            highlightcolor=self.colors["accent"],
            font=(self.font_family, self.font_size),
        )
        self.input.pack(side="left", fill="both", expand=True, padx=(14, 8), pady=10)
        self.input.bind("<Control-Return>", self.send_message_event)
        self.input.bind("<Control-Shift-V>", self.paste_files_event)
        self.input.bind("<Button-3>", self.show_input_menu)

        button_frame = tk.Frame(input_outer, bg=self.colors["panel"])
        button_frame.pack(side="right", fill="y", padx=(0, 14), pady=10)
        self.send_button = self.make_button(button_frame, "送信", self.send_message)
        self.send_button.pack(fill="x", expand=True)
        self.stop_button = self.make_button(button_frame, "停止", self.stop_generation, danger=True)
        self.stop_button.pack(fill="x", expand=True, pady=(6, 0))
        self.stop_button.configure(state="disabled")

        if DND_AVAILABLE:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind("<<Drop>>", self.on_drop)
                self.input.drop_target_register(DND_FILES)
                self.input.dnd_bind("<<Drop>>", self.on_drop)
                self.chat.drop_target_register(DND_FILES)
                self.chat.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass

    def build_menu(self):
        menu = tk.Menu(self.root, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        file_menu = tk.Menu(menu, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        file_menu.add_command(label="新しい会話", command=self.new_conversation)
        file_menu.add_command(label="会話をエクスポート", command=self.export_conversation)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.on_close)
        menu.add_cascade(label="ファイル", menu=file_menu)

        edit = tk.Menu(menu, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        edit.add_command(label="コピー", command=lambda: self.root.event_generate("<<Copy>>"))
        edit.add_command(label="貼り付け", command=lambda: self.root.event_generate("<<Paste>>"))
        edit.add_command(label="すべて選択", command=lambda: self.root.event_generate("<<SelectAll>>"))
        menu.add_cascade(label="編集", menu=edit)

        view = tk.Menu(menu, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        view.add_command(label="サイドバー表示切替", command=self.toggle_sidebar)
        view.add_command(label="入力欄をクリア", command=lambda: self.input.delete("1.0", "end"))
        menu.add_cascade(label="表示", menu=view)

        project = tk.Menu(menu, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        project.add_command(label="新しいプロジェクト", command=self.create_project)
        project.add_command(label="プロジェクト名変更", command=self.rename_project)
        project.add_command(label="プロジェクトフォルダを開く", command=self.open_project_folder)
        menu.add_cascade(label="プロジェクト", menu=project)

        settings = tk.Menu(menu, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        settings.add_command(label="設定", command=self.open_settings)
        menu.add_cascade(label="設定", menu=settings)

        help_menu = tk.Menu(menu, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        help_menu.add_command(label="使い方", command=self.show_help)
        help_menu.add_command(label="バージョン情報", command=lambda: messagebox.showinfo("Local LLM GUI", "Local LLM GUI Release V1.0\nPython + Node.js + Ollama"))
        menu.add_cascade(label="ヘルプ", menu=help_menu)
        self.root.configure(menu=menu)

    def toggle_sidebar(self):
        sidebar = getattr(self, "sidebar", None)
        if sidebar is None:
            return
        if sidebar.winfo_ismapped():
            sidebar.pack_forget()
        else:
            sidebar.pack(side="left", fill="y", before=self.main)

    def setup_chat_tags(self):
        self.chat.tag_configure("user_name", font=(self.font_family, self.font_size, "bold"), foreground="#7fb0ff", spacing1=12)
        self.chat.tag_configure("ai_name", font=(self.font_family, self.font_size, "bold"), foreground="#b9a1ff", spacing1=18)
        self.chat.tag_configure("body", font=(self.font_family, self.font_size), foreground=self.colors["text"], spacing3=5)
        self.chat.tag_configure("heading1", font=(self.font_family, self.font_size + 7, "bold"), foreground=self.colors["text"], spacing1=12, spacing3=8)
        self.chat.tag_configure("heading2", font=(self.font_family, self.font_size + 4, "bold"), foreground=self.colors["text"], spacing1=10, spacing3=6)
        self.chat.tag_configure("heading3", font=(self.font_family, self.font_size + 2, "bold"), foreground=self.colors["text"], spacing1=8, spacing3=5)
        self.chat.tag_configure("bold", font=(self.font_family, self.font_size, "bold"))
        self.chat.tag_configure("italic", font=(self.font_family, self.font_size, "italic"))
        self.chat.tag_configure("bullet", lmargin1=18, lmargin2=34, spacing3=3)
        self.chat.tag_configure("quote", foreground="#aeb8c4", lmargin1=18, lmargin2=30, spacing3=4)
        self.chat.tag_configure("code", font=("Consolas", max(self.font_size - 1, 9)), background=self.colors["code"], foreground=self.colors["code_text"], lmargin1=18, lmargin2=18, spacing1=5, spacing3=5)
        self.chat.tag_configure("inline_code", font=("Consolas", max(self.font_size - 1, 9)), background=self.colors["inline"], foreground="#e6d2ff")
        self.chat.tag_configure("link", foreground="#75a8ff", underline=True)


    def project_dir(self, project_id: str) -> Path:
        return PROJECTS_ROOT / project_filename(project_id)

    def project_meta_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def load_projects(self):
        PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
        if not self.project_meta_path(DEFAULT_PROJECT_ID).is_file():
            d = self.project_dir(DEFAULT_PROJECT_ID)
            (d / "conversations").mkdir(parents=True, exist_ok=True)
            self.project_meta_path(DEFAULT_PROJECT_ID).write_text(json.dumps({"id": DEFAULT_PROJECT_ID, "name": "既定のプロジェクト", "description": ""}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.projects.clear()
        for meta in PROJECTS_ROOT.glob("*/project.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                pid = str(data.get("id", meta.parent.name))
                self.projects[pid] = Project(pid, str(data.get("name", pid)), str(data.get("description", "")))
            except Exception:
                continue
        if DEFAULT_PROJECT_ID not in self.projects:
            self.projects[DEFAULT_PROJECT_ID] = Project(DEFAULT_PROJECT_ID, "既定のプロジェクト")
        self.refresh_project_combo()

    def refresh_project_combo(self):
        project = self.projects.get(self.current_project_id, self.projects[DEFAULT_PROJECT_ID])
        self.project_var.set(project.name)
        self.project_current_label.configure(text=project.name)

    def choose_project(self):
        window = tk.Toplevel(self.root)
        window.title("プロジェクトを選択")
        window.geometry("430x430")
        window.minsize(360, 320)
        window.configure(bg=self.colors["bg"])
        window.transient(self.root)
        window.grab_set()

        tk.Label(window, text="プロジェクトを選択", bg=self.colors["bg"], fg=self.colors["text"], font=(self.font_family, 15, "bold"), anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        tk.Label(window, text="使用するプロジェクトを選択してください。", bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, 9), anchor="w").pack(fill="x", padx=18, pady=(0, 12))

        list_frame = tk.Frame(window, bg=self.colors["bg"])
        list_frame.pack(fill="both", expand=True, padx=16)
        listbox = tk.Listbox(list_frame, bg=self.colors["sidebar2"], fg=self.colors["text"], selectbackground=self.colors["accent"], selectforeground="white", activestyle="none", relief="flat", highlightthickness=1, highlightbackground=self.colors["border"], font=(self.font_family, self.font_size), exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scroll.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scroll.set)

        ids = list(self.projects)
        for pid in ids:
            project = self.projects[pid]
            count = len(list((self.project_dir(pid) / "conversations").glob("*.json"))) if (self.project_dir(pid) / "conversations").is_dir() else 0
            listbox.insert("end", f"{project.name}    ({count} 会話)")
        if self.current_project_id in ids:
            listbox.selection_set(ids.index(self.current_project_id))
            listbox.see(ids.index(self.current_project_id))

        def select_and_close(_event=None):
            selection = listbox.curselection()
            if not selection:
                return
            self.select_project(ids[selection[0]])
            window.destroy()

        listbox.bind("<Double-Button-1>", select_and_close)
        buttons = tk.Frame(window, bg=self.colors["bg"])
        buttons.pack(fill="x", padx=16, pady=14)
        self.make_button(buttons, "キャンセル", window.destroy, small=True).pack(side="right", padx=(6, 0))
        self.make_button(buttons, "選択", select_and_close, small=True).pack(side="right")

    def project_id_from_name(self, name: str) -> str | None:
        for pid, project in self.projects.items():
            if project.name == name:
                return pid
        return None

    def select_project(self, project_id: str):
        if project_id not in self.projects:
            project_id = DEFAULT_PROJECT_ID
        self.current_project_id = project_id
        d = self.project_dir(project_id)
        (d / "conversations").mkdir(parents=True, exist_ok=True)
        self.conversations.clear()
        self.current_id = None
        self.load_conversations()
        if self.conversations:
            self.select_conversation(next(iter(self.conversations)))
        else:
            self.new_conversation()
        self.refresh_project_combo()

    def create_project(self):
        name = simpledialog.askstring("新しいプロジェクト", "プロジェクト名:", parent=self.root)
        if not name or not name.strip():
            return
        pid = project_filename(name.strip())
        if pid in self.projects:
            messagebox.showwarning("プロジェクト", "同名のプロジェクトが既にあります。")
            return
        d = self.project_dir(pid)
        (d / "conversations").mkdir(parents=True, exist_ok=True)
        self.project_meta_path(pid).write_text(json.dumps({"id": pid, "name": name.strip(), "description": ""}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.load_projects()
        self.select_project(pid)

    def rename_project(self):
        project = self.projects.get(self.current_project_id)
        if not project:
            return
        name = simpledialog.askstring("プロジェクト名変更", "新しい名前:", initialvalue=project.name, parent=self.root)
        if not name or not name.strip():
            return
        project.name = name.strip()
        self.project_meta_path(project.project_id).write_text(json.dumps({"id": project.project_id, "name": project.name, "description": project.description}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.refresh_project_combo()

    def open_project_folder(self):
        path = self.project_dir(self.current_project_id)
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(path)
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("プロジェクト", str(e))


    def conversation_root(self) -> Path:
        return self.project_dir(self.current_project_id) / "conversations"

    def conversation_path(self, conversation: Conversation) -> Path:
        return self.conversation_root() / f"{safe_filename(conversation.conversation_id)}.json"

    def load_conversations(self):
        root = self.conversation_root()
        root.mkdir(parents=True, exist_ok=True)
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                conversation = Conversation.from_dict(data)
                if conversation.conversation_id:
                    self.conversations[conversation.conversation_id] = conversation
            except Exception:
                continue
        self.refresh_conversation_list()

    def save_conversation(self, conversation: Conversation):
        self.conversation_root().mkdir(parents=True, exist_ok=True)
        self.conversation_path(conversation).write_text(json.dumps(conversation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def new_conversation(self):
        conversation_id = time.strftime("%Y%m%d_%H%M%S") + "_" + str(int(time.time() * 1000) % 1000)
        conversation = Conversation(conversation_id=conversation_id, model=self.model_var.get().strip())
        self.conversations[conversation.conversation_id] = conversation
        self.save_conversation(conversation)
        self.refresh_conversation_list()
        self.select_conversation(conversation.conversation_id)

    def delete_conversation(self):
        if not self.current_id:
            return
        conversation = self.conversations.get(self.current_id)
        if not conversation:
            return
        if not messagebox.askyesno("会話の削除", f"「{conversation.title}」を削除しますか？", parent=self.root):
            return
        try:
            self.conversation_path(conversation).unlink(missing_ok=True)
        except OSError:
            pass
        self.conversations.pop(conversation.conversation_id, None)
        self.current_id = None
        self.refresh_conversation_list()
        if self.conversations:
            self.select_conversation(next(iter(self.conversations)))
        else:
            self.new_conversation()

    def rename_conversation(self):
        conversation = self.conversations.get(self.current_id or "")
        if not conversation:
            return
        title = simpledialog.askstring("会話名", "会話名:", initialvalue=conversation.title, parent=self.root)
        if title and title.strip():
            conversation.title = title.strip()
            self.save_conversation(conversation)
            self.refresh_conversation_list()
            self.select_conversation(conversation.conversation_id)

    def refresh_conversation_list(self):
        self.conversation_list.delete(0, "end")
        for conversation in self.conversations.values():
            self.conversation_list.insert("end", conversation.title)

    def select_conversation(self, conversation_id: str):
        if conversation_id not in self.conversations:
            return
        self.current_id = conversation_id
        conversation = self.conversations[conversation_id]
        if conversation.model:
            self.model_var.set(conversation.model)
        self.render_conversation()
        ids = list(self.conversations)
        index = ids.index(conversation_id)
        self.conversation_list.selection_clear(0, "end")
        self.conversation_list.selection_set(index)
        self.conversation_list.see(index)

    def on_conversation_selected(self, _event):
        selection = self.conversation_list.curselection()
        if not selection:
            return
        ids = list(self.conversations)
        if 0 <= selection[0] < len(ids):
            self.select_conversation(ids[selection[0]])


    def parse_drop_files(self, data: str) -> list[Path]:
        try:
            raw = self.root.tk.splitlist(data)
        except Exception:
            raw = [data]
        return [Path(x) for x in raw]

    def on_drop(self, event):
        self.add_attachment_paths(self.parse_drop_files(event.data))

    def add_attachments(self):
        files = filedialog.askopenfilenames(title="添付ファイルを選択", parent=self.root)
        if files:
            self.add_attachment_paths([Path(f) for f in files])

    def paste_files_event(self, _event):
        self.add_attachments()
        return "break"

    def add_attachment_paths(self, paths: list[Path]):
        added = 0
        for path in paths:
            try:
                path = path.resolve()
                if not path.is_file() or path in self.attachments:
                    continue
                size = path.stat().st_size
                if path.suffix.lower() in IMAGE_EXTENSIONS and size > MAX_IMAGE_ATTACHMENT:
                    messagebox.showwarning("添付ファイル", f"画像が大きすぎます（10MBまで）:\n{path.name}", parent=self.root)
                    continue
                if path.suffix.lower() in TEXT_EXTENSIONS and size > MAX_TEXT_ATTACHMENT:
                    messagebox.showwarning("添付ファイル", f"テキストが大きすぎます（2MBまで）:\n{path.name}", parent=self.root)
                    continue
                self.attachments.append(path)
                added += 1
            except OSError:
                continue
        self.update_attachment_label()
        if added:
            self.status_var.set(f"添付: {len(self.attachments)}件")

    def clear_attachments(self):
        self.attachments.clear()
        self.update_attachment_label()

    def update_attachment_label(self):
        if not self.attachments:
            self.attachment_label.configure(text="なし")
            return
        names = [p.name for p in self.attachments]
        text = ", ".join(names[:3])
        if len(names) > 3:
            text += f" ほか{len(names) - 3}件"
        self.attachment_label.configure(text=text)

    def prepare_messages(self, conversation: Conversation) -> list[dict]:
        result = []
        for message in conversation.messages[:-1]:
            result.append({"role": message.role, "content": message.content})
        return result

    def attachment_payload(self) -> tuple[str, list[str], list[dict]]:
        extra_text = []
        images = []
        metadata = []
        for path in self.attachments:
            ext = path.suffix.lower()
            metadata.append({"name": path.name, "path": str(path)})
            if ext in IMAGE_EXTENSIONS:
                try:
                    images.append(base64.b64encode(path.read_bytes()).decode("ascii"))
                except OSError:
                    pass
            elif ext in TEXT_EXTENSIONS:
                try:
                    data = path.read_bytes()
                    text, encoding = decode_text_bytes(data)
                    extra_text.append(f"\n\n--- 添付ファイル: {path.name} ({encoding}) ---\n{text}\n--- 添付ファイル終端 ---")
                except OSError:
                    pass
        return "".join(extra_text), images, metadata


    def clear_chat(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.code_widgets.clear()
        self.chat.configure(state="disabled")

    def render_conversation(self):
        self.clear_chat()
        conversation = self.conversations.get(self.current_id or "")
        if not conversation:
            return
        for message in conversation.messages:
            self.insert_message(message.role, message.content, message.attachments)
        self.scroll_chat()

    def insert_message(self, role: str, content: str, attachments: list[dict] | None = None):
        self.chat.configure(state="normal")
        self.chat.insert("end", "あなた\n" if role == "user" else "AI\n", "user_name" if role == "user" else "ai_name")
        if attachments:
            self.chat.insert("end", "添付: " + ", ".join(str(a.get("name", "")) for a in attachments) + "\n", "quote")
        self.insert_markdown(content)
        self.chat.insert("end", "\n", "body")
        self.chat.configure(state="disabled")

    def insert_markdown(self, text: str):
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        in_code = False
        code_lines: list[str] = []
        language = ""
        for line in lines:
            fence = re.match(r"^\s*```\s*([\w+-]*)\s*$", line)
            if fence:
                if in_code:
                    self.insert_code_block("\n".join(code_lines), language)
                    code_lines = []
                    language = ""
                    in_code = False
                else:
                    in_code = True
                    language = fence.group(1)
                continue
            if in_code:
                code_lines.append(line)
                continue
            heading = re.match(r"^\s*(#{1,6})\s+(.*?)\s*#*\s*$", line)
            if heading:
                level = min(len(heading.group(1)), 3)
                self.insert_inline_markdown(heading.group(2), f"heading{level}")
                self.chat.insert("end", "\n", f"heading{level}")
                continue
            if re.match(r"^\s*>\s?", line):
                content = re.sub(r"^\s*>\s?", "", line)
                self.insert_inline_markdown(content, "quote")
                self.chat.insert("end", "\n", "quote")
                continue
            bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
            if bullet:
                self.insert_inline_markdown("• " + bullet.group(1), "bullet")
                self.chat.insert("end", "\n", "bullet")
                continue
            numbered = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
            if numbered:
                self.insert_inline_markdown(f"{numbered.group(1)}. {numbered.group(2)}", "bullet")
                self.chat.insert("end", "\n", "bullet")
                continue
            if re.match(r"^\s*([-*_])(?:\s*\1){2,}\s*$", line):
                self.chat.insert("end", "────────────────────────\n", "quote")
                continue
            self.insert_inline_markdown(line, "body")
            self.chat.insert("end", "\n", "body")
        if in_code:
            self.insert_code_block("\n".join(code_lines), language)

    def insert_inline_markdown(self, line: str, base_tag="body"):
        pattern = re.compile(r"(\*\*.+?\*\*|__.+?__|~~.+?~~|(?<!\*)\*[^*\n]+\*(?!\*)|`[^`\n]+`|\[[^\]]+\]\([^\)]+\))")
        position = 0
        for match in pattern.finditer(line):
            if match.start() > position:
                self.chat.insert("end", line[position:match.start()], base_tag)
            token = match.group(0)
            if token.startswith(("**", "__")):
                self.chat.insert("end", token[2:-2], "bold")
            elif token.startswith("~~"):
                self.chat.insert("end", token[2:-2], "quote")
            elif token.startswith("*") and token.endswith("*"):
                self.chat.insert("end", token[1:-1], "italic")
            elif token.startswith("`"):
                self.chat.insert("end", token[1:-1], "inline_code")
            else:
                m = re.match(r"\[([^\]]+)\]\(([^\)]+)\)", token)
                if m:
                    self.chat.insert("end", m.group(1), "link")
                else:
                    self.chat.insert("end", token, base_tag)
            position = match.end()
        if position < len(line):
            self.chat.insert("end", line[position:], base_tag)

    def insert_code_block(self, code: str, language: str = ""):
        frame = tk.Frame(self.chat, bg=self.colors["code"], bd=0)
        top = tk.Frame(frame, bg=self.colors["code"])
        top.pack(fill="x")
        label = tk.Label(top, text=language or "code", bg=self.colors["code"], fg=self.colors["muted"], font=(self.font_family, 8))
        label.pack(side="left", padx=8, pady=4)
        copy_button = tk.Button(top, text="コピー", command=lambda c=code: self.copy_code(c), bg=self.colors["panel2"], fg=self.colors["text"], activebackground=self.colors["accent"], relief="flat", bd=0, padx=7, pady=2, font=(self.font_family, 8), cursor="hand2")
        copy_button.pack(side="right", padx=6, pady=3)
        text = tk.Text(frame, height=max(2, min(14, code.count("\n") + 1)), wrap="none", bg=self.colors["code"], fg=self.colors["code_text"], insertbackground=self.colors["code_text"], relief="flat", bd=0, font=("Consolas", max(self.font_size - 1, 9)), padx=8, pady=5)
        text.pack(fill="both", expand=True)
        text.insert("1.0", code)
        text.configure(state="disabled")
        self.chat.window_create("end", window=frame, padx=8, pady=6)
        self.chat.insert("end", "\n", "body")
        self.code_widgets.append((copy_button, code))

    def copy_code(self, code: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        self.root.update()
        self.status_var.set("コードをコピーしました")
        self.root.after(1500, lambda: self.status_var.set(self.last_status))

    def scroll_chat(self):
        self.chat.see("end")


    def refresh_models(self):
        threading.Thread(target=self.fetch_models, daemon=True).start()

    def fetch_models(self):
        try:
            result = request_json(self.bridge_base + "/models", timeout=5)
            models = [str(item.get("name", "")) for item in result.get("models", []) if isinstance(item, dict) and item.get("name")]
            self.events.put(("models", models))
        except Exception as error:
            self.events.put(("status", f"Ollama未接続: {error}"))

    def install_model(self):
        name = simpledialog.askstring("モデルの追加", "Ollamaモデル名（例: qwen3:14b）:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if any(ord(c) < 32 for c in name):
            return
        self.status_var.set(f"モデル取得中: {name}")
        self.make_model_pull_thread(name)

    def make_model_pull_thread(self, name: str):
        threading.Thread(target=self.pull_model, args=(name,), daemon=True).start()

    def pull_model(self, name: str):
        payload = json.dumps({"name": name, "stream": True}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.bridge_base + "/pull", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3600) as response:
                while True:
                    raw = response.readline()
                    if not raw:
                        break
                    try:
                        item = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if item.get("error"):
                        self.events.put(("pull_error", str(item["error"])))
                        return
                    status = item.get("status") or ""
                    if status:
                        self.events.put(("pull_status", f"モデル取得中: {name} / {status}"))
                    if item.get("done"):
                        break
            self.events.put(("pull_done", name))
        except Exception as e:
            self.events.put(("pull_error", str(e)))


    def detect_vision_model(self, preferred: str) -> str | None:
        try:
            result = request_json(self.bridge_base + "/vision-model", {"preferred": preferred}, timeout=15)
            if result.get("ok") and result.get("model"):
                return str(result["model"])
        except Exception:
            return None
        return None

    def send_message_event(self, _event):
        self.send_message()
        return "break"

    def send_message(self):
        if self.generating:
            return
        text = self.input.get("1.0", "end").strip()
        if not text and not self.attachments:
            return
        model = self.model_var.get().strip()
        if not model:
            messagebox.showwarning("モデル未指定", "モデル名を指定してください。", parent=self.root)
            return
        conversation = self.conversations.get(self.current_id or "")
        if not conversation:
            self.new_conversation()
            conversation = self.conversations[self.current_id]

        extra_text, images, metadata = self.attachment_payload()
        if images:
            vision_model = self.detect_vision_model(model)
            if not vision_model:
                messagebox.showwarning("画像を添付", "画像を理解できるVision対応モデルが見つかりません。\nVision対応モデルをOllamaに追加してから再度お試しください。", parent=self.root)
                return
            if vision_model != model:
                model = vision_model
                self.model_var.set(model)
                self.status_var.set(f"画像対応モデルへ自動切替: {model}")
        final_text = text + extra_text
        if not final_text:
            final_text = "添付ファイルを確認してください。"
        self.input.delete("1.0", "end")
        conversation.model = model
        conversation.messages.append(Message("user", final_text, metadata))
        if conversation.title == "新しい会話":
            first_line = text.splitlines()[0].strip() if text else (metadata[0]["name"] if metadata else "新しい会話")
            conversation.title = first_line[:34] or "新しい会話"
        conversation.messages.append(Message("assistant", ""))
        self.save_conversation(conversation)
        self.refresh_conversation_list()
        self.select_conversation(conversation.conversation_id)
        self.attachments.clear()
        self.update_attachment_label()

        messages = self.prepare_messages(conversation)
        if images:
            messages[-1]["images"] = images
        self.generating = True
        self.stop_requested = False
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.last_status = f"生成中: {model}"
        self.status_var.set(self.last_status)
        self.stream_thread = threading.Thread(target=self.stream_chat, args=(conversation.conversation_id, model, messages), daemon=True)
        self.stream_thread.start()

    def stream_chat(self, conversation_id: str, model: str, messages: list[dict]):
        payload = json.dumps({"model": model, "messages": messages}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.bridge_base + "/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=3600) as response:
                while not self.stop_requested:
                    raw = response.readline()
                    if not raw:
                        break
                    try:
                        item = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if item.get("error"):
                        self.events.put(("error", str(item["error"])))
                        break
                    content = item.get("message", {}).get("content", "")
                    if content:
                        self.events.put(("token", conversation_id, content))
                    if item.get("done"):
                        break
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(error)
            self.events.put(("error", f"HTTP {error.code}: {detail}"))
        except Exception as error:
            if not self.stop_requested:
                self.events.put(("error", str(error)))
        finally:
            self.events.put(("generation_done", conversation_id))

    def stop_generation(self):
        if not self.generating:
            return
        self.stop_requested = True
        threading.Thread(target=self.send_stop, daemon=True).start()
        self.status_var.set("停止しています...")

    def send_stop(self):
        try:
            request_json(self.bridge_base + "/stop", payload={}, timeout=3)
        except Exception:
            pass


    def export_conversation(self):
        conversation = self.conversations.get(self.current_id or "")
        if not conversation:
            return
        path = filedialog.asksaveasfilename(title="会話をエクスポート", defaultextension=".json", filetypes=[("JSON", "*.json"), ("Markdown", "*.md")], parent=self.root)
        if not path:
            return
        try:
            if Path(path).suffix.lower() == ".md":
                parts = [f"# {conversation.title}\n"]
                for m in conversation.messages:
                    parts.append(("## あなた\n" if m.role == "user" else "## AI\n") + m.content + "\n")
                Path(path).write_text("\n".join(parts), encoding="utf-8")
            else:
                Path(path).write_text(json.dumps(conversation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_var.set("エクスポートしました")
        except OSError as e:
            messagebox.showerror("エクスポート", str(e), parent=self.root)

    def save_runtime_settings(self, host: str, port: str, default_model: str) -> None:
        host = host.strip()
        port = port.strip()
        default_model = default_model.strip()
        if not host:
            raise ValueError("Ollamaホストを入力してください。")
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("Ollamaポートが正しくありません。")
        self.config["ollama_host"] = host
        self.config["ollama_port"] = port
        self.config["default_model"] = default_model
        save_config(self.config)
        payload = {"host": host, "port": int(port)}
        request_json(self.bridge_base + "/set-ollama", payload=payload, timeout=5)
        self.last_status = "Ollama設定を更新しました"
        self.status_var.set(self.last_status)
        self.refresh_models()

    def open_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("設定")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=self.colors["bg"])

        frame = tk.Frame(dialog, bg=self.colors["bg"], padx=24, pady=20)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="Ollama", bg=self.colors["bg"], fg=self.colors["text"], font=(self.font_family, 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        tk.Label(frame, text="ホスト", bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, self.font_size)).grid(row=1, column=0, sticky="w", pady=5)
        host_var = tk.StringVar(value=self.config.get("ollama_host", "127.0.0.1"))
        host_entry = tk.Entry(frame, textvariable=host_var, bg=self.colors["input"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", width=28, font=(self.font_family, self.font_size))
        host_entry.grid(row=1, column=1, sticky="ew", pady=5)
        tk.Label(frame, text="ポート", bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, self.font_size)).grid(row=2, column=0, sticky="w", pady=5)
        port_var = tk.StringVar(value=self.config.get("ollama_port", "11434"))
        tk.Entry(frame, textvariable=port_var, bg=self.colors["input"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", width=28, font=(self.font_family, self.font_size)).grid(row=2, column=1, sticky="ew", pady=5)
        tk.Label(frame, text="既定モデル", bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, self.font_size)).grid(row=3, column=0, sticky="w", pady=5)
        model_var = tk.StringVar(value=self.config.get("default_model", ""))
        tk.Entry(frame, textvariable=model_var, bg=self.colors["input"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", width=28, font=(self.font_family, self.font_size)).grid(row=3, column=1, sticky="ew", pady=5)

        status = tk.StringVar(value="")
        tk.Label(frame, textvariable=status, bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, 9), wraplength=360, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 5))

        buttons = tk.Frame(frame, bg=self.colors["bg"])
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def test():
            host = host_var.get().strip()
            port = port_var.get().strip()
            if not port.isdigit():
                status.set("ポート番号を確認してください。")
                return
            url = ollama_url({"ollama_host": host, "ollama_port": port})
            try:
                result = request_json(url + "/api/tags", timeout=3)
                count = len(result.get("models", [])) if isinstance(result, dict) else 0
                status.set(f"接続成功: {url} / {count}モデル")
            except Exception as error:
                status.set(f"接続失敗: {error}")

        def save():
            try:
                self.save_runtime_settings(host_var.get(), port_var.get(), model_var.get())
            except Exception as error:
                status.set(str(error))
                return
            dialog.destroy()

        self.make_button(buttons, "接続テスト", test, small=True).pack(side="left", padx=4)
        self.make_button(buttons, "キャンセル", dialog.destroy, small=True).pack(side="left", padx=4)
        self.make_button(buttons, "保存", save, small=True).pack(side="left", padx=4)
        host_entry.focus_set()
        dialog.bind("<Return>", lambda _event: save())
        self.root.wait_window(dialog)

    def show_help(self):
        messagebox.showinfo("Local LLM GUI の使い方", "Ctrl+Enter: 送信\nドラッグ＆ドロップ: ファイル添付\nコードブロック: コピー可能\nモデル欄の「＋モデル」: Ollamaモデルを追加\n\nテキストファイルは内容をLLMへ渡し、対応モデルでは画像も渡します。", parent=self.root)

    def show_input_menu(self, event):
        menu = tk.Menu(self.root, tearoff=False, bg=self.colors["panel"], fg=self.colors["text"], activebackground=self.colors["accent"])
        menu.add_command(label="コピー", command=lambda: self.input.event_generate("<<Copy>>"))
        menu.add_command(label="貼り付け", command=lambda: self.input.event_generate("<<Paste>>"))
        menu.add_command(label="添付ファイルを追加", command=self.add_attachments)
        menu.tk_popup(event.x_root, event.y_root)


    def process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "models":
                    models = event[1]
                    self.models = models
                    self.model_combo["values"] = models
                    current = self.model_var.get().strip()
                    if current not in models:
                        default = self.config["default_model"]
                        if default in models:
                            self.model_var.set(default)
                        elif models:
                            self.model_var.set(models[0])
                    self.last_status = f"Ollama接続済み / {len(models)}モデル"
                    self.status_var.set(self.last_status)
                elif kind == "status":
                    self.last_status = event[1]
                    self.status_var.set(event[1])
                elif kind == "pull_status":
                    self.status_var.set(event[1])
                elif kind == "pull_done":
                    self.last_status = f"モデル追加完了: {event[1]}"
                    self.status_var.set(self.last_status)
                    self.refresh_models()
                elif kind == "pull_error":
                    self.last_status = "モデル追加失敗"
                    self.status_var.set(self.last_status)
                    messagebox.showerror("モデル追加", event[1], parent=self.root)
                elif kind == "token":
                    conversation_id, token = event[1], event[2]
                    conversation = self.conversations.get(conversation_id)
                    if not conversation or not conversation.messages or conversation.messages[-1].role != "assistant":
                        continue
                    conversation.messages[-1].content += token
                    if conversation_id == self.current_id:
                        self.render_conversation()
                        self.scroll_chat()
                elif kind == "error":
                    self.last_status = "生成エラー"
                    self.status_var.set(self.last_status)
                    message = str(event[1])
                    conversation = self.conversations.get(self.current_id or "")
                    if conversation and conversation.messages and conversation.messages[-1].role == "assistant" and not conversation.messages[-1].content:
                        conversation.messages[-1].content = "エラー: " + message
                        self.save_conversation(conversation)
                        self.render_conversation()
                    messagebox.showerror("生成エラー", message, parent=self.root)
                elif kind == "generation_done":
                    conversation_id = event[1]
                    conversation = self.conversations.get(conversation_id)
                    if conversation:
                        self.save_conversation(conversation)
                    if conversation_id == self.current_id:
                        self.generating = False
                        self.send_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                        self.last_status = "生成を停止しました" if self.stop_requested else "待機中"
                        self.status_var.set(self.last_status)
                        self.stop_requested = False
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def on_close(self):
        if self.generating:
            self.stop_requested = True
        for conversation in self.conversations.values():
            self.save_conversation(conversation)
        self.root.destroy()


def create_root():
    if DND_AVAILABLE:
        try:
            return TkinterDnD.Tk()
        except Exception:
            pass
    return tk.Tk()


def main():
    root = create_root()
    LocalLLMGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
