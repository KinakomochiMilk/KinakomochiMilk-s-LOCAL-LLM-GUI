from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import json
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "gui_config.txt"
NODE_SERVER = ROOT / "node" / "server.js"
GUI = ROOT / "gui" / "app.py"

DEFAULTS = {
    "title": "Local LLM GUI",
    "width": "1200",
    "height": "820",
    "ollama_host": "127.0.0.1",
    "ollama_port": "11434",
    "bridge_host": "127.0.0.1",
    "bridge_port": "18767",
    "default_model": "",
    "font_family": "Yu Gothic UI",
    "font_size": "11",
    "theme": "dark",
    "setup_complete": "false",
}

def parse_config(path: Path) -> dict[str, str]:
    result = DEFAULTS.copy()
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in result:
                result[key.strip()] = value.strip()
    return result

def save_config(config: dict[str, str]) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    order = ["title", "width", "height", "ollama_host", "ollama_port", "bridge_host", "bridge_port", "default_model", "font_family", "font_size", "theme", "setup_complete"]
    CONFIG.write_text("\n".join(f"{key}={config[key]}" for key in order) + "\n", encoding="utf-8")

def check_ollama(host: str, port: int) -> tuple[bool, list[str]]:
    base = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=1.2) as response:
            data = json.loads(response.read().decode("utf-8"))
            models = [str(item.get("name")) for item in data.get("models", []) if isinstance(item, dict) and item.get("name")]
            return 200 <= response.status < 300, models
    except Exception:
        return False, []

def detect_ollama() -> list[tuple[str, int, list[str]]]:
    candidates = []
    env = os.environ.get("OLLAMA_HOST", "").strip()
    if env:
        if "://" not in env:
            env = "http://" + env
        from urllib.parse import urlparse
        parsed = urlparse(env)
        if parsed.hostname:
            port = parsed.port or 11434
            ok, models = check_ollama(parsed.hostname, port)
            if ok:
                candidates.append((parsed.hostname, port, models))
    for port in [11434, 11435, 11436, 11437, 11438, 11439, 11440]:
        if any(item[1] == port and item[0] == "127.0.0.1" for item in candidates):
            continue
        ok, models = check_ollama("127.0.0.1", port)
        if ok:
            candidates.append(("127.0.0.1", port, models))
    return candidates

def first_run_setup(config: dict[str, str]) -> bool:
    if config.get("setup_complete", "false").lower() in {"true", "1", "yes", "on"}:
        return True
    root = tk.Tk()
    root.title("Local LLM GUI - 初回設定")
    root.geometry("560x430")
    root.resizable(False, False)
    root.configure(bg="#14171b")
    colors = {"bg":"#14171b", "panel":"#20242a", "input":"#1b1f24", "text":"#eef1f4", "muted":"#9da6b0", "accent":"#5b8def"}
    detected = detect_ollama()
    state = {"saved": False, "detected": detected}
    frame = tk.Frame(root, bg=colors["bg"], padx=28, pady=24)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="Local LLM GUI", bg=colors["bg"], fg=colors["text"], font=("Yu Gothic UI", 20, "bold")).pack(anchor="w")
    tk.Label(frame, text="初回設定", bg=colors["bg"], fg=colors["muted"], font=("Yu Gothic UI", 11)).pack(anchor="w", pady=(0, 18))
    card = tk.Frame(frame, bg=colors["panel"], padx=16, pady=14)
    card.pack(fill="x")
    tk.Label(card, text="Ollama接続先", bg=colors["panel"], fg=colors["text"], font=("Yu Gothic UI", 11, "bold")).grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,10))
    host_var = tk.StringVar(value=config.get("ollama_host", "127.0.0.1"))
    port_var = tk.StringVar(value=config.get("ollama_port", "11434"))
    model_var = tk.StringVar(value=config.get("default_model", ""))
    status = tk.StringVar(value="Ollamaを自動検出しています..." if not detected else "Ollamaを検出しました。")
    tk.Label(card,text="ホスト",bg=colors["panel"],fg=colors["muted"],font=("Yu Gothic UI",10)).grid(row=1,column=0,sticky="w",pady=5)
    tk.Entry(card,textvariable=host_var,bg=colors["input"],fg=colors["text"],insertbackground=colors["text"],relief="flat",width=28).grid(row=1,column=1,sticky="ew",pady=5)
    tk.Label(card,text="ポート",bg=colors["panel"],fg=colors["muted"],font=("Yu Gothic UI",10)).grid(row=2,column=0,sticky="w",pady=5)
    tk.Entry(card,textvariable=port_var,bg=colors["input"],fg=colors["text"],insertbackground=colors["text"],relief="flat",width=28).grid(row=2,column=1,sticky="ew",pady=5)
    tk.Label(card,text="既定モデル",bg=colors["panel"],fg=colors["muted"],font=("Yu Gothic UI",10)).grid(row=3,column=0,sticky="w",pady=5)
    model_combo=ttk.Combobox(card,textvariable=model_var,state="normal",width=26)
    model_combo.grid(row=3,column=1,sticky="ew",pady=5)
    card.columnconfigure(1,weight=1)
    if detected:
        host_var.set(detected[0][0]); port_var.set(str(detected[0][1]))
        model_combo["values"] = detected[0][2]
        if detected[0][2] and not model_var.get():
            model_var.set(detected[0][2][0])
        status.set(f"自動検出: {detected[0][0]}:{detected[0][1]} / {len(detected[0][2])}モデル")
    tk.Label(frame,textvariable=status,bg=colors["bg"],fg=colors["muted"],font=("Yu Gothic UI",9),wraplength=500,justify="left").pack(anchor="w",pady=(14,8))
    buttons=tk.Frame(frame,bg=colors["bg"]); buttons.pack(fill="x",pady=(10,0))
    def test():
        host=host_var.get().strip(); port=port_var.get().strip()
        if not port.isdigit(): status.set("ポート番号を確認してください。"); return
        ok, models=check_ollama(host,int(port))
        if ok:
            model_combo["values"]=models
            if models and not model_var.get(): model_var.set(models[0])
            status.set(f"接続成功: {host}:{port} / {len(models)}モデル")
        else: status.set("Ollamaへ接続できませんでした。ホストとポートを確認してください。")
    def save():
        host=host_var.get().strip(); port=port_var.get().strip()
        if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            status.set("ホストとポートを確認してください。"); return
        config["ollama_host"]=host; config["ollama_port"]=port; config["default_model"]=model_var.get().strip(); config["setup_complete"]="true"
        save_config(config); state["saved"]=True; root.destroy()
    def cancel():
        if messagebox.askyesno("初回設定", "設定を完了せずに終了しますか？", parent=root): root.destroy()
    def btn(text, command):
        return tk.Button(buttons,text=text,command=command,bg=colors["panel"],fg=colors["text"],activebackground=colors["accent"],activeforeground="white",relief="flat",bd=0,padx=12,pady=7,font=("Yu Gothic UI",10,"bold"),cursor="hand2")
    btn("接続テスト",test).pack(side="left",padx=3); btn("キャンセル",cancel).pack(side="right",padx=3); btn("設定して起動",save).pack(side="right",padx=3)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.mainloop()
    return state["saved"]

def show_error(title: str, message: str) -> None:
    try:
        root = tk.Tk(); root.withdraw(); messagebox.showerror(title, message); root.destroy()
    except Exception:
        print(f"{title}: {message}", file=sys.stderr)

def python_ok() -> bool:
    return sys.version_info >= (3, 9)

def start_node(config: dict[str, str]) -> subprocess.Popen | None:
    node = shutil.which("node")
    if not node: return None
    env = os.environ.copy()
    env["BRIDGE_HOST"] = config.get("bridge_host", "127.0.0.1")
    env["BRIDGE_PORT"] = config.get("bridge_port", "18767")
    env["OLLAMA_HOST"] = f"http://{config.get('ollama_host','127.0.0.1')}:{config.get('ollama_port','11434')}"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen([node,str(NODE_SERVER)],cwd=str(ROOT),env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=creationflags)

def bridge_alive(config: dict[str, str]) -> bool:
    host=config.get("bridge_host","127.0.0.1"); port=config.get("bridge_port","18767")
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health",timeout=1.0) as response: return response.status in (200,503)
    except Exception: return False

def ensure_optional_python_packages() -> None:
    try:
        import tkinterdnd2
        return
    except ImportError: pass
    try: answer=input("ドラッグ＆ドロップ添付には tkinterdnd2 が必要です。インストールしますか？ [Y/N]: ").strip().lower()
    except (EOFError,KeyboardInterrupt): return
    if answer not in {"y","yes"}: return
    try: subprocess.run([sys.executable,"-m","pip","install","-r",str(ROOT/"requirements.txt")],cwd=str(ROOT),check=True)
    except Exception as error: show_error("Local LLM GUI","オプション機能のインストールに失敗しました。\n\n"+str(error))

def main() -> int:
    if not python_ok(): show_error("Local LLM GUI","Python 3.9以降が必要です。"); return 1
    if not NODE_SERVER.is_file(): show_error("Local LLM GUI",f"Node.jsサーバーが見つかりません:\n{NODE_SERVER}"); return 1
    if not GUI.is_file(): show_error("Local LLM GUI",f"GUIファイルが見つかりません:\n{GUI}"); return 1
    config=parse_config(CONFIG)
    if not first_run_setup(config): return 0
    node=shutil.which("node")
    if not node: show_error("Local LLM GUI","Node.jsが見つかりません。\n\nNode.jsをインストールしてから、もう一度起動してください。"); return 1
    ensure_optional_python_packages()
    bridge=start_node(config)
    if bridge is None: show_error("Local LLM GUI","Node.jsブリッジを起動できませんでした。"); return 1
    for _ in range(30):
        if bridge_alive(config): break
        time.sleep(0.1)
    else:
        bridge.terminate(); show_error("Local LLM GUI","Node.jsブリッジを起動できませんでした。\nポートが使用中の可能性があります。"); return 1
    try: subprocess.run([sys.executable,str(GUI)],cwd=str(ROOT),check=False)
    finally:
        if bridge.poll() is None:
            bridge.terminate()
            try: bridge.wait(timeout=2)
            except subprocess.TimeoutExpired: bridge.kill()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
