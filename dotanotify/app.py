import json
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from dotanotify import core
from dotanotify.instant import InstantMonitor, check_steam_key


class DotaNotifyApp:
    def __init__(self, root):
        self.root = root
        self._install_edit_bindings()
        self.root.title("DotaNotify — Dota Watchlist")
        self.root.geometry("900x620")
        self.root.minsize(820, 560)
        self.root.option_add("*Font", ("Tahoma", 10))

        self.events = queue.Queue()
        self.cfg = core.load_config()
        self.watchlist = core.load_watchlist()
        self.monitor = core.Monitor(self.cfg, self.get_watchlist, self.log)
        self.instant_monitor = InstantMonitor(
            self.cfg,
            self.get_watchlist,
            self.log,
            self._send_instant_alert,
            on_found=self._instant_players_found,
        )
        self._build_ui()
        self._refresh_watchlist()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self._drain_events)
        if self.cfg.my_account_id:
            self.start_monitor()

    def _install_edit_bindings(self):
        def select_all(widget):
            widget.focus_set()
            if isinstance(widget, tk.Text):
                widget.tag_add("sel", "1.0", "end-1c")
            else:
                widget.select_range(0, "end")
                widget.icursor("end")

        def select_all_shortcut(event):
            select_all(event.widget)
            return "break"

        def control_key(event):
            if event.keysym.lower() in ("v", "c", "x", "a"):
                return None
            if sys.platform != "win32":
                return None
            virtual = {
                86: "<<Paste>>",
                67: "<<Copy>>",
                88: "<<Cut>>",
                65: "select_all",
            }.get(event.keycode)
            if virtual == "select_all":
                select_all(event.widget)
                return "break"
            if virtual:
                event.widget.event_generate(virtual)
                return "break"
            return None

        def show_context_menu(event):
            widget = event.widget
            menu = tk.Menu(widget, tearoff=0)

            def generate_virtual(sequence):
                widget.focus_set()
                widget.event_generate(sequence)

            menu.add_command(label="ตัด", command=lambda: generate_virtual("<<Cut>>"))
            menu.add_command(label="คัดลอก", command=lambda: generate_virtual("<<Copy>>"))
            menu.add_command(label="วาง", command=lambda: generate_virtual("<<Paste>>"))
            menu.add_command(label="เลือกทั้งหมด", command=lambda: select_all(widget))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        for cls in ("TEntry", "Entry", "Text"):
            self.root.bind_class(cls, "<Control-KeyPress>", control_key)
            self.root.bind_class(cls, "<Control-a>", select_all_shortcut)
            self.root.bind_class(cls, "<Control-A>", select_all_shortcut)
            self.root.bind_class(cls, "<Button-3>", show_context_menu)

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
        self.monitor_status = tk.StringVar(value="สถานะ: หยุด")
        ttk.Label(status, textvariable=self.monitor_status).pack(side="left")

        buttons = ttk.Frame(self.home_tab)
        buttons.pack(fill="x", pady=(0, 12))
        self.monitor_button = ttk.Button(
            buttons, text="เริ่มทำงาน", command=self.toggle_monitor
        )
        self.monitor_button.pack(side="left")

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
        self.tree.bind("<Double-1>", self._open_player_page)
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
        ttk.Label(form, text="ชื่อ").grid(row=0, column=2, sticky="w", padx=3, pady=3)
        ttk.Entry(form, textvariable=self.player_name_var).grid(
            row=0, column=3, sticky="ew", padx=3, pady=3
        )
        ttk.Label(form, text="แท็ก").grid(row=1, column=0, sticky="w", padx=3, pady=3)
        ttk.Combobox(
            form,
            textvariable=self.tag_var,
            values=("", "danger", "smurf", "toxic", "เล่นดี", "เพื่อน"),
        ).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(form, text="Note").grid(row=1, column=2, sticky="w", padx=3, pady=3)
        ttk.Entry(form, textvariable=self.note_var).grid(
            row=1, column=3, sticky="ew", padx=3, pady=3
        )
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=2)

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

    def _build_settings(self):
        form = ttk.Frame(self.settings_tab)
        form.pack(fill="x")
        self.setting_vars = {
            "my_account_id": tk.StringVar(value=self.cfg.my_account_id),
            "line_channel_token": tk.StringVar(value=self.cfg.line_channel_token),
            "line_user_id": tk.StringVar(value=self.cfg.line_user_id),
            "steam_api_key": tk.StringVar(value=self.cfg.steam_api_key),
        }
        labels = [
            ("my_account_id", "My Steam32 Account ID (หลายบัญชีคั่นด้วย ,)"),
            ("line_channel_token", "LINE Channel Access Token"),
            ("line_user_id", "LINE User ID"),
            ("steam_api_key", "Steam Web API Key"),
        ]
        for row, (key, label) in enumerate(labels):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=5)
            widget = ttk.Entry(form, textvariable=self.setting_vars[key], width=54)
            if key in ("line_channel_token", "steam_api_key"):
                widget.configure(show="•")
            widget.grid(row=row, column=1, sticky="ew", padx=4, pady=5)
        form.columnconfigure(1, weight=1)

        actions = ttk.Frame(self.settings_tab)
        actions.pack(fill="x", pady=(8, 10))
        ttk.Button(actions, text="บันทึก", command=self.save_settings).pack(
            side="left", padx=3
        )
        ttk.Button(actions, text="ทดสอบ", command=self.test_settings).pack(
            side="left", padx=3
        )

        help_text = (
            "Steam32 Account ID: ดูตัวเลขหลัง /players/ ที่ opendota.com/players/<id>; "
            "หลายบัญชีคั่นด้วยจุลภาค (,)\n"
            "LINE: ใช้ Messaging API Channel Access Token และ Your User ID จาก "
            "Basic settings (ขึ้นต้นด้วย U)\n"
            "แจ้งเตือนทันที: ใช้ Steam Web API Key จาก steamcommunity.com/dev/apikey "
            "(Domain: localhost) และใส่ -condebug ใน Launch Options ของ Dota 2"
        )
        ttk.Label(self.settings_tab, text=help_text, wraplength=840, justify="left").pack(
            anchor="w", pady=8
        )

    def toggle_monitor(self):
        if self.monitor.is_running or self.instant_monitor.is_running:
            self.monitor.stop()
            self.instant_monitor.stop()
            self._update_status()
            self.log("หยุดทำงานแล้ว")
            return
        if not self.save_settings(show_message=False):
            return
        if not self.cfg.my_account_id:
            messagebox.showerror(
                "เริ่มทำงานไม่ได้",
                "กรุณากรอก My Steam32 Account ID ในแท็บตั้งค่าก่อน",
            )
            return
        self.start_monitor()

    def start_monitor(self):
        if not self.cfg.my_account_id:
            return
        self.monitor.start()
        if self.cfg.steam_api_key:
            self.instant_monitor.start()
        else:
            self.log("ไม่มี Steam Web API Key — ใช้เฉพาะเช็คหลังจบเกม")
        self._update_status()

    def _send_instant_alert(self, alert):
        return core.deliver_alert(self.cfg, alert, self.log)

    def _instant_players_found(self, alerts):
        self.events.put(
            ("dialog", "info", "เจอผู้เล่นใน Watchlist", "\n\n".join(alerts))
        )

    def _update_status(self):
        running = self.monitor.is_running or self.instant_monitor.is_running
        status = "สถานะ: กำลังทำงาน" if running else "สถานะ: หยุด"
        if self.instant_monitor.is_running:
            status += " + แจ้งเตือนทันที"
        self.monitor_status.set(status)
        self.monitor_button.configure(
            text="หยุดทำงาน" if running else "เริ่มทำงาน"
        )

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
        if not player["name"]:
            self._lookup_missing_player_name(str(account_id))

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

    def _lookup_missing_player_name(self, account_id):
        def lookup():
            try:
                result = core.lookup_player(account_id)
                name = str((result or {}).get("personaname", "")).strip()
                self.events.put(("player_lookup", account_id, name, ""))
            except Exception as error:
                self.events.put(("player_lookup", account_id, "", str(error)))

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

    def _open_player_page(self, event):
        account_id = self.tree.identify_row(event.y)
        if not account_id:
            return
        webbrowser.open(f"https://www.opendota.com/players/{account_id}")

    def _save_watchlist(self):
        core.save_watchlist(self.watchlist)

    def save_settings(self, show_message=True):
        try:
            values = {
                "check_interval_min": self.cfg.check_interval_min,
                "gsi_port": self.cfg.gsi_port,
                "gsi_token": self.cfg.gsi_token,
                "auto_start": self.cfg.auto_start,
            }
            values.update({
                name: variable.get()
                for name, variable in self.setting_vars.items()
            })
            settings = core.parse_settings(values)
        except (ValueError, tk.TclError) as error:
            messagebox.showerror("การตั้งค่าไม่ถูกต้อง", str(error))
            return False

        try:
            updated_cfg = replace(self.cfg, **settings)
            core.save_config(updated_cfg)
            self.cfg.my_account_id = updated_cfg.my_account_id
            self.cfg.line_channel_token = updated_cfg.line_channel_token
            self.cfg.line_user_id = updated_cfg.line_user_id
            self.cfg.check_interval_min = updated_cfg.check_interval_min
            self.cfg.gsi_port = updated_cfg.gsi_port
            self.cfg.gsi_token = updated_cfg.gsi_token
            self.cfg.auto_start = updated_cfg.auto_start
            self.cfg.steam_api_key = updated_cfg.steam_api_key
            if show_message:
                messagebox.showinfo("บันทึกแล้ว", "บันทึกการตั้งค่าเรียบร้อย")
                if not self.cfg.my_account_id:
                    if self.monitor.is_running or self.instant_monitor.is_running:
                        self.monitor.stop()
                        self.instant_monitor.stop()
                elif not self.monitor.is_running:
                    self.start_monitor()
                elif self.cfg.steam_api_key and not self.instant_monitor.is_running:
                    self.instant_monitor.start()
                    self.log("เริ่มแจ้งเตือนทันทีแล้ว")
                elif not self.cfg.steam_api_key and self.instant_monitor.is_running:
                    self.instant_monitor.stop()
                self._update_status()
            return True
        except OSError as error:
            messagebox.showerror("การตั้งค่าไม่ถูกต้อง", str(error))
            return False

    def test_settings(self):
        if not self.save_settings(show_message=False):
            return
        cfg = replace(self.cfg)

        def run_tests():
            results = []
            try:
                line_ok, line_info = core.send_line(
                    cfg.line_channel_token,
                    cfg.line_user_id,
                    "🛡️ ทดสอบการแจ้งเตือนจาก DotaNotify สำเร็จ!",
                )
            except Exception as error:
                line_ok, line_info = False, str(error)
            results.append(("LINE", line_ok, line_info))
            if cfg.steam_api_key:
                steam_ok, steam_info = check_steam_key(cfg.steam_api_key)
                results.append(("Steam API Key", steam_ok, steam_info))
            message = "\n".join(
                f"{label}: {'ผ่าน' if ok else 'ไม่ผ่าน'} — {info}"
                for label, ok, info in results
            )
            level = "error" if any(not ok for _label, ok, _info in results) else "info"
            self.events.put(("dialog", level, "ผลการทดสอบ", message))

        threading.Thread(target=run_tests, daemon=True).start()

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
            elif event[0] == "player_lookup":
                account_id, name, error = event[1:]
                player = next(
                    (
                        item
                        for item in self.watchlist.get("players", [])
                        if str(item.get("account_id", "")) == account_id
                    ),
                    None,
                )
                if player and name and not str(player.get("name", "")).strip():
                    player["name"] = name
                    if (
                        self.account_id_var.get().strip() == account_id
                        and not self.player_name_var.get().strip()
                    ):
                        self.player_name_var.set(name)
                    self._save_watchlist()
                    self._refresh_watchlist()
                    self.log(f"ค้นหาผู้เล่นสำเร็จ: {name}")
                elif player and not str(player.get("name", "")).strip():
                    self.log(
                        error
                        or f"ไม่พบชื่อผู้เล่นใน OpenDota: Account ID {account_id}"
                    )
            elif event[0] == "dialog":
                if event[2] == "เจอผู้เล่นใน Watchlist":
                    self.root.bell()
                if event[1] == "error":
                    messagebox.showerror(event[2], event[3])
                else:
                    messagebox.showinfo(event[2], event[3])
        self._update_status()
        self.root.after(200, self._drain_events)

    def close(self):
        self.monitor.stop()
        self.instant_monitor.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    DotaNotifyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
