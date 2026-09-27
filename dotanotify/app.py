import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from dotanotify import core
from dotanotify.gsi import GSIServer, find_dota_cfg_dirs, install_gsi_cfg


class DotaNotifyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DotaNotify — Dota Watchlist")
        self.root.geometry("900x620")
        self.root.minsize(820, 560)
        self.root.option_add("*Font", ("Tahoma", 10))

        self.events = queue.Queue()
        self.cfg = core.load_config()
        self.watchlist = core.load_watchlist()
        self.monitor = core.Monitor(self.cfg, self.get_watchlist, self.log)
        self.gsi_server = None
        self._build_ui()
        self._refresh_watchlist()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self._drain_events)
        if self.cfg.auto_start:
            self.start_monitor()

    def log(self, message):
        self.events.put(("log", str(message)))

    def get_watchlist(self):
        return self.watchlist

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.home_tab = ttk.Frame(notebook, padding=12)
        self.watchlist_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.home_tab, text="หน้าหลัก")
        notebook.add(self.watchlist_tab, text="Watchlist")
        notebook.add(self.settings_tab, text="ตั้งค่า")

        self._build_home()
        self._build_watchlist()
        self._build_settings()

    def _build_home(self):
        status = ttk.Frame(self.home_tab)
        status.pack(fill="x", pady=(0, 12))
        self.monitor_status = tk.StringVar(value="หยุด")
        self.gsi_status = tk.StringVar(value="ปิด")
        ttk.Label(status, text="เช็คอัตโนมัติ:").pack(side="left")
        ttk.Label(status, textvariable=self.monitor_status).pack(side="left", padx=(4, 18))
        ttk.Label(status, text="Live GSI:").pack(side="left")
        ttk.Label(status, textvariable=self.gsi_status).pack(side="left", padx=4)

        buttons = ttk.Frame(self.home_tab)
        buttons.pack(fill="x", pady=(0, 12))
        self.monitor_button = ttk.Button(
            buttons, text="เริ่มเช็คอัตโนมัติ", command=self.toggle_monitor
        )
        self.monitor_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="เช็คตอนนี้", command=self.monitor.check_now).pack(
            side="left", padx=4
        )
        self.gsi_button = ttk.Button(
            buttons, text="เปิด Live GSI", command=self.toggle_gsi
        )
        self.gsi_button.pack(side="left", padx=4)

        ttk.Label(self.home_tab, text="บันทึกการทำงาน").pack(anchor="w")
        self.log_text = ScrolledText(self.home_tab, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

    def _build_watchlist(self):
        columns = ("account_id", "name", "tag", "note", "added")
        self.tree = ttk.Treeview(
            self.watchlist_tab, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "account_id": "Account ID",
            "name": "ชื่อ",
            "tag": "แท็ก",
            "note": "Note",
            "added": "เพิ่มเมื่อ",
        }
        widths = {"account_id": 110, "name": 190, "tag": 90, "note": 270, "added": 115}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=60)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select_player)
        self.count_label = ttk.Label(self.watchlist_tab, text="ผู้เล่น: 0 คน")
        self.count_label.pack(anchor="w", pady=(5, 3))

        form = ttk.Frame(self.watchlist_tab)
        form.pack(fill="x")
        self.account_id_var = tk.StringVar()
        self.player_name_var = tk.StringVar()
        self.tag_var = tk.StringVar()
        self.note_var = tk.StringVar()

        ttk.Label(form, text="Account ID").grid(row=0, column=0, sticky="w", padx=3, pady=3)
        ttk.Entry(form, textvariable=self.account_id_var, width=20).grid(
            row=0, column=1, sticky="ew", padx=3, pady=3
        )
        ttk.Button(form, text="ค้นหา OpenDota", command=self.lookup_player).grid(
            row=0, column=2, padx=3, pady=3
        )
        ttk.Label(form, text="ชื่อ").grid(row=0, column=3, sticky="w", padx=3, pady=3)
        ttk.Entry(form, textvariable=self.player_name_var).grid(
            row=0, column=4, sticky="ew", padx=3, pady=3
        )
        ttk.Label(form, text="แท็ก").grid(row=1, column=0, sticky="w", padx=3, pady=3)
        ttk.Combobox(
            form,
            textvariable=self.tag_var,
            values=("", "danger", "smurf", "toxic", "เล่นดี", "เพื่อน"),
        ).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(form, text="Note").grid(row=1, column=2, sticky="w", padx=3, pady=3)
        ttk.Entry(form, textvariable=self.note_var).grid(
            row=1, column=3, columnspan=2, sticky="ew", padx=3, pady=3
        )
        form.columnconfigure(4, weight=1)

        actions = ttk.Frame(self.watchlist_tab)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="เพิ่ม/อัปเดต", command=self.upsert_player).pack(
            side="left", padx=3
        )
        ttk.Button(actions, text="ลบ", command=self.delete_player).pack(
            side="left", padx=3
        )
        ttk.Button(actions, text="Import JSON", command=self.import_watchlist).pack(
            side="left", padx=3
        )
        ttk.Button(actions, text="Export JSON", command=self.export_watchlist).pack(
            side="left", padx=3
        )
        ttk.Button(actions, text="เปิด OpenDota", command=self.open_opendota).pack(
            side="left", padx=3
        )

    def _build_settings(self):
        form = ttk.Frame(self.settings_tab)
        form.pack(fill="x")
        self.setting_vars = {
            "my_account_id": tk.StringVar(value=self.cfg.my_account_id),
            "line_channel_token": tk.StringVar(value=self.cfg.line_channel_token),
            "line_user_id": tk.StringVar(value=self.cfg.line_user_id),
            "check_interval_min": tk.StringVar(value=str(self.cfg.check_interval_min)),
            "gsi_port": tk.StringVar(value=str(self.cfg.gsi_port)),
            "gsi_token": tk.StringVar(value=self.cfg.gsi_token),
            "auto_start": tk.BooleanVar(value=self.cfg.auto_start),
        }
        labels = [
            ("my_account_id", "My Steam32 Account ID"),
            ("line_channel_token", "LINE Channel Access Token"),
            ("line_user_id", "LINE User ID"),
            ("check_interval_min", "ช่วงเวลาเช็ค (นาที)"),
            ("gsi_port", "GSI Port"),
            ("gsi_token", "GSI Token"),
        ]
        self.token_entry = None
        for row, (key, label) in enumerate(labels):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=5)
            if key == "check_interval_min":
                widget = ttk.Spinbox(
                    form,
                    from_=1,
                    to=120,
                    textvariable=self.setting_vars[key],
                    width=12,
                )
            elif key == "gsi_port":
                widget = ttk.Spinbox(
                    form,
                    from_=1,
                    to=65535,
                    textvariable=self.setting_vars[key],
                    width=12,
                )
            else:
                widget = ttk.Entry(form, textvariable=self.setting_vars[key], width=54)
                if key == "line_channel_token":
                    widget.configure(show="•")
                    self.token_entry = widget
            widget.grid(row=row, column=1, sticky="ew", padx=4, pady=5)
        ttk.Checkbutton(
            form,
            text="แสดง Token",
            command=self._toggle_token_visibility,
        ).grid(row=1, column=2, sticky="w", padx=5)
        ttk.Checkbutton(
            form,
            text="เริ่มเช็คอัตโนมัติเมื่อเปิดโปรแกรม",
            variable=self.setting_vars["auto_start"],
        ).grid(row=len(labels), column=1, sticky="w", padx=4, pady=5)
        form.columnconfigure(1, weight=1)

        actions = ttk.Frame(self.settings_tab)
        actions.pack(fill="x", pady=(8, 10))
        ttk.Button(actions, text="บันทึก", command=self.save_settings).pack(
            side="left", padx=3
        )
        ttk.Button(actions, text="ทดสอบส่ง LINE", command=self.test_line).pack(
            side="left", padx=3
        )
        ttk.Button(
            actions,
            text="ติดตั้ง GSI config ให้ Dota 2",
            command=self.install_gsi,
        ).pack(side="left", padx=3)
        ttk.Button(
            actions, text="เปิดโฟลเดอร์ข้อมูล", command=self.open_data_folder
        ).pack(side="left", padx=3)

        help_text = (
            "Steam32 Account ID: ดูตัวเลขหลัง /players/ จากหน้าโปรไฟล์ OpenDota "
            "(เช่น opendota.com/players/12345678)\n"
            "LINE: สร้าง Messaging API Channel ใน LINE Developers แล้วใช้ Channel "
            "Access Token และ User ID ของผู้รับ\n"
            "หมายเหตุ: Live GSI จะส่งข้อมูล allplayers เมื่อกำลัง spectate/watch "
            "เท่านั้น ไม่ส่งขณะเล่นเอง; การเช็คหลังจบแมทช์ใช้ได้ขณะเล่น"
        )
        ttk.Label(self.settings_tab, text=help_text, wraplength=840, justify="left").pack(
            anchor="w", pady=8
        )

    def toggle_monitor(self):
        if self.monitor.is_running:
            self.monitor.stop()
            self.monitor_status.set("หยุด")
            self.monitor_button.configure(text="เริ่มเช็คอัตโนมัติ")
            self.log("หยุดเช็คอัตโนมัติแล้ว")
        else:
            self.start_monitor()

    def start_monitor(self):
        if not self.cfg.my_account_id:
            self.log("คำเตือน: ยังไม่ได้ตั้งค่า My Steam32 Account ID")
        self.monitor.start()
        self.monitor_status.set("กำลังทำงาน")
        self.monitor_button.configure(text="⏹ หยุด")

    def toggle_gsi(self):
        if self.gsi_server and self.gsi_server.is_running:
            self.gsi_server.stop()
            self.gsi_server = None
            self.gsi_status.set("ปิด")
            self.gsi_button.configure(text="เปิด Live GSI")
            self.log("ปิด Live GSI แล้ว")
            return
        self.save_settings(show_message=False)
        try:
            self.gsi_server = GSIServer(
                self.cfg.gsi_port,
                self.cfg.gsi_token,
                self.get_watchlist,
                self._send_live_alert,
                self.log,
            )
            self.gsi_server.start()
            self.gsi_status.set(f"เปิด (Port {self.gsi_server.port})")
            self.gsi_button.configure(text="ปิด Live GSI")
        except OSError as error:
            self.gsi_server = None
            messagebox.showerror("เปิด Live GSI ไม่สำเร็จ", str(error))

    def _send_live_alert(self, alert):
        ok, info = core.send_line(
            self.cfg.line_channel_token, self.cfg.line_user_id, alert
        )
        self.log(info if ok else f"LINE: {info}")

    def _refresh_watchlist(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for player in self.watchlist.get("players", []):
            account_id = str(player.get("account_id", ""))
            self.tree.insert(
                "",
                "end",
                iid=account_id,
                values=(
                    account_id,
                    player.get("name", ""),
                    player.get("tag", ""),
                    player.get("note", ""),
                    player.get("added", "")[:10],
                ),
            )
        self.count_label.configure(text=f"ผู้เล่น: {len(self.watchlist.get('players', []))} คน")

    def _select_player(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        account_id = selection[0]
        player = next(
            (
                item
                for item in self.watchlist.get("players", [])
                if str(item.get("account_id")) == account_id
            ),
            None,
        )
        if player:
            self.account_id_var.set(account_id)
            self.player_name_var.set(player.get("name", ""))
            self.tag_var.set(player.get("tag", ""))
            self.note_var.set(player.get("note", ""))

    def upsert_player(self):
        account_text = self.account_id_var.get().strip()
        if not account_text.isdigit():
            messagebox.showerror("ข้อมูลไม่ถูกต้อง", "Account ID ต้องเป็นตัวเลข")
            return
        account_id = int(account_text)
        players = self.watchlist.setdefault("players", [])
        existing = next(
            (
                player
                for player in players
                if str(player.get("account_id", "")) == str(account_id)
            ),
            None,
        )
        added = (
            existing.get("added")
            if existing
            else datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        player = {
            "account_id": account_id,
            "name": self.player_name_var.get().strip(),
            "tag": self.tag_var.get().strip(),
            "note": self.note_var.get().strip(),
            "added": added,
        }
        if existing:
            existing.update(player)
        else:
            players.append(player)
        self._save_watchlist()
        self._refresh_watchlist()

    def delete_player(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("ลบผู้เล่น", "กรุณาเลือกผู้เล่นที่ต้องการลบ")
            return
        account_id = selection[0]
        if not messagebox.askyesno("ยืนยันการลบ", f"ลบ Account ID {account_id} หรือไม่?"):
            return
        self.watchlist["players"] = [
            player
            for player in self.watchlist.get("players", [])
            if str(player.get("account_id")) != account_id
        ]
        self._save_watchlist()
        self._refresh_watchlist()

    def lookup_player(self):
        account_id = self.account_id_var.get().strip()
        if not account_id.isdigit():
            messagebox.showerror("ข้อมูลไม่ถูกต้อง", "กรุณากรอก Account ID เป็นตัวเลข")
            return

        def lookup():
            result = core.lookup_player(account_id)
            if result:
                self.events.put(("lookup", result.get("personaname", "")))
            else:
                self.events.put(("dialog", "error", "ค้นหาไม่พบ", "ไม่พบผู้เล่นจาก OpenDota"))

        threading.Thread(target=lookup, daemon=True).start()

    def import_watchlist(self):
        path = filedialog.askopenfilename(
            title="Import Watchlist",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as file:
                imported = json.load(file)
            imported_players = imported.get("players", []) if isinstance(imported, dict) else imported
            if not isinstance(imported_players, list):
                raise ValueError("รูปแบบ JSON ไม่ถูกต้อง")
            merged = {
                str(player["account_id"]): player
                for player in self.watchlist.get("players", [])
                if isinstance(player, dict) and "account_id" in player
            }
            for player in imported_players:
                if not isinstance(player, dict) or not str(player.get("account_id", "")).isdigit():
                    continue
                account_id = int(player["account_id"])
                previous = merged.get(str(account_id), {})
                merged[str(account_id)] = {
                    "account_id": account_id,
                    "name": player.get("name", previous.get("name", "")),
                    "tag": player.get("tag", previous.get("tag", "")),
                    "note": player.get("note", previous.get("note", "")),
                    "added": player.get("added", previous.get("added", "")),
                }
            self.watchlist = {"players": list(merged.values())}
            self._save_watchlist()
            self._refresh_watchlist()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            messagebox.showerror("Import ไม่สำเร็จ", str(error))

    def export_watchlist(self):
        path = filedialog.asksaveasfilename(
            title="Export Watchlist",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            core.save_json(path, self.watchlist)
            messagebox.showinfo("Export สำเร็จ", f"บันทึกไฟล์แล้ว:\n{path}")
        except OSError as error:
            messagebox.showerror("Export ไม่สำเร็จ", str(error))

    def open_opendota(self):
        selection = self.tree.selection()
        account_id = selection[0] if selection else self.account_id_var.get().strip()
        if not account_id.isdigit():
            messagebox.showinfo("OpenDota", "กรุณาเลือกผู้เล่นหรือกรอก Account ID")
            return
        webbrowser.open(f"https://www.opendota.com/players/{account_id}")

    def _save_watchlist(self):
        core.save_watchlist(self.watchlist)

    def save_settings(self, show_message=True):
        try:
            self.cfg.my_account_id = self.setting_vars["my_account_id"].get().strip()
            self.cfg.line_channel_token = self.setting_vars["line_channel_token"].get().strip()
            self.cfg.line_user_id = self.setting_vars["line_user_id"].get().strip()
            self.cfg.check_interval_min = int(
                self.setting_vars["check_interval_min"].get()
            )
            self.cfg.gsi_port = int(self.setting_vars["gsi_port"].get())
            self.cfg.gsi_token = self.setting_vars["gsi_token"].get().strip()
            self.cfg.auto_start = bool(self.setting_vars["auto_start"].get())
            if not 1 <= self.cfg.check_interval_min <= 120:
                raise ValueError("ช่วงเวลาเช็คต้องอยู่ระหว่าง 1–120 นาที")
            if not 1 <= self.cfg.gsi_port <= 65535:
                raise ValueError("GSI Port ต้องอยู่ระหว่าง 1–65535")
            core.save_config(self.cfg)
            if show_message:
                messagebox.showinfo("บันทึกแล้ว", "บันทึกการตั้งค่าเรียบร้อย")
            return True
        except (ValueError, tk.TclError) as error:
            messagebox.showerror("การตั้งค่าไม่ถูกต้อง", str(error))
            return False

    def _toggle_token_visibility(self):
        self.token_entry.configure(show="" if self.token_entry.cget("show") else "•")

    def test_line(self):
        if not self.save_settings(show_message=False):
            return
        cfg = self.cfg

        def send_test():
            ok, info = core.send_line(
                cfg.line_channel_token,
                cfg.line_user_id,
                "🛡️ ทดสอบการแจ้งเตือนจาก DotaNotify สำเร็จ!",
            )
            self.events.put(
                ("dialog", "info" if ok else "error", "ทดสอบ LINE", info)
            )

        threading.Thread(target=send_test, daemon=True).start()

    def install_gsi(self):
        if not self.save_settings(show_message=False):
            return
        directories = find_dota_cfg_dirs()
        cfg_dir = directories[0] if directories else None
        if cfg_dir is None:
            selected = filedialog.askdirectory(
                title="เลือกโฟลเดอร์ ...\\dota 2 beta\\game\\dota\\cfg"
            )
            if not selected:
                return
            cfg_dir = selected
        try:
            path = install_gsi_cfg(cfg_dir, self.cfg.gsi_port, self.cfg.gsi_token)
            messagebox.showinfo(
                "ติดตั้ง GSI config สำเร็จ",
                f"บันทึกไฟล์แล้ว:\n{path}\n\nโปรดรีสตาร์ท Dota 2 เพื่อเริ่มใช้งาน",
            )
        except OSError as error:
            messagebox.showerror("ติดตั้งไม่สำเร็จ", str(error))

    def open_data_folder(self):
        path = str(core.data_dir())
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as error:
            messagebox.showerror("เปิดโฟลเดอร์ไม่สำเร็จ", str(error))

    def _drain_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "log":
                timestamp = datetime.now().strftime("%H:%M:%S")
                self.log_text.configure(state="normal")
                self.log_text.insert("end", f"[{timestamp}] {event[1]}\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            elif event[0] == "lookup":
                self.player_name_var.set(event[1])
                self.log(f"ค้นหาผู้เล่นสำเร็จ: {event[1]}")
            elif event[0] == "dialog":
                if event[1] == "error":
                    messagebox.showerror(event[2], event[3])
                else:
                    messagebox.showinfo(event[2], event[3])
        if self.monitor.is_running:
            self.monitor_status.set("กำลังทำงาน")
            self.monitor_button.configure(text="หยุด")
        else:
            self.monitor_status.set("หยุด")
            self.monitor_button.configure(text="เริ่มเช็คอัตโนมัติ")
        if self.gsi_server and self.gsi_server.is_running:
            self.gsi_status.set(f"เปิด (Port {self.gsi_server.port})")
        self.root.after(200, self._drain_events)

    def close(self):
        self.monitor.stop()
        if self.gsi_server:
            self.gsi_server.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    DotaNotifyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
