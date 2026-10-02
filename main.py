"""Tkinter interface for the direct TCP peer-to-peer assignment."""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from p2p_node import P2PNode


BASE_DIR = Path(__file__).resolve().parent


class PeerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("P2P Network - Text and File Sharing")
        self.root.geometry("920x610")
        self.root.minsize(760, 500)
        self.node = None
        self.events = queue.Queue()
        self.peer_ids = []
        self._build_ui()
        self.root.after(100, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        startup = ttk.LabelFrame(outer, text="My Peer", padding=10)
        startup.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        startup.columnconfigure(1, weight=1)
        ttk.Label(startup, text="Name").grid(row=0, column=0, padx=(0, 6))
        self.name_entry = ttk.Entry(startup, width=22)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(startup, text="Port").grid(row=0, column=2, padx=(0, 6))
        self.port_entry = ttk.Entry(startup, width=9)
        self.port_entry.grid(row=0, column=3, padx=(0, 12))
        self.start_button = ttk.Button(startup, text="Start Peer", command=self._start)
        self.start_button.grid(row=0, column=4, padx=(0, 6))
        self.stop_button = ttk.Button(startup, text="Stop", command=self._stop, state="disabled")
        self.stop_button.grid(row=0, column=5)

        connect = ttk.LabelFrame(outer, text="Connect to Another Peer", padding=10)
        connect.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        connect.columnconfigure(1, weight=1)
        ttk.Label(connect, text="IPv4 address").grid(row=0, column=0, padx=(0, 6))
        self.ip_entry = ttk.Entry(connect)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(connect, text="Port").grid(row=0, column=2, padx=(0, 6))
        self.remote_port_entry = ttk.Entry(connect, width=9)
        self.remote_port_entry.grid(row=0, column=3, padx=(0, 12))
        self.connect_button = ttk.Button(connect, text="Connect", command=self._connect, state="disabled")
        self.connect_button.grid(row=0, column=4)

        middle = ttk.Frame(outer)
        middle.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        middle.columnconfigure(0, weight=1, minsize=265)
        middle.columnconfigure(1, weight=2)
        middle.rowconfigure(0, weight=1)

        peers_box = ttk.LabelFrame(middle, text="Connected Peers", padding=8)
        peers_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        peers_box.rowconfigure(0, weight=1)
        peers_box.columnconfigure(0, weight=1)
        self.peer_list = tk.Listbox(peers_box, exportselection=False, activestyle="none")
        self.peer_list.grid(row=0, column=0, sticky="nsew")
        self.peer_list.bind("<<ListboxSelect>>", lambda _event: self._update_send_buttons())
        peer_scroll = ttk.Scrollbar(peers_box, orient="vertical", command=self.peer_list.yview)
        peer_scroll.grid(row=0, column=1, sticky="ns")
        self.peer_list.configure(yscrollcommand=peer_scroll.set)

        log_box = ttk.LabelFrame(middle, text="Messages / Events", padding=8)
        log_box.grid(row=0, column=1, sticky="nsew")
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)
        self.log = tk.Text(log_box, wrap="word", state="disabled", height=12)
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)

        bottom = ttk.LabelFrame(outer, text="Send to Selected Peer", padding=10)
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        self.message_entry = ttk.Entry(bottom)
        self.message_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.message_entry.bind("<Return>", lambda _event: self._send_text())
        self.send_button = ttk.Button(bottom, text="Send Text", command=self._send_text, state="disabled")
        self.send_button.grid(row=0, column=1, padx=(0, 8))
        self.file_button = ttk.Button(bottom, text="Choose File and Send",
                                      command=self._send_file, state="disabled")
        self.file_button.grid(row=0, column=2)

        self._log("Enter your name and listening port, then start the peer.")

    def _log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        try:
            node = P2PNode(self.name_entry.get(), self.port_entry.get(),
                           BASE_DIR / "downloads", self._enqueue)
            node.start()
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror("Cannot start peer", str(exc))
            return
        self.node = node
        self.name_entry.configure(state="disabled")
        self.port_entry.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.connect_button.configure(state="normal")

    def _stop(self):
        if self.node:
            self.node.stop()
            self.node = None
        self.peer_list.delete(0, "end")
        self.peer_ids = []
        self.name_entry.configure(state="normal")
        self.port_entry.configure(state="normal")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self._update_send_buttons()

    def _enqueue(self, kind, details):
        self.events.put((kind, details))

    def _run_background(self, action):
        def task():
            try:
                action()
            except (OSError, ValueError, RuntimeError, ConnectionError) as exc:
                self.events.put(("ui_error", {"message": str(exc)}))
        threading.Thread(target=task, daemon=True).start()

    def _connect(self):
        node = self.node
        if node:
            ip, port = self.ip_entry.get(), self.remote_port_entry.get()
            self._run_background(lambda: node.connect(ip, port))

    def _selected_peer(self):
        selection = self.peer_list.curselection()
        if not selection:
            raise ValueError("select a connected peer first")
        return self.peer_ids[selection[0]]

    def _send_text(self):
        if not self.node:
            return
        try:
            peer_id = self._selected_peer()
        except ValueError as exc:
            messagebox.showerror("No peer selected", str(exc))
            return
        message = self.message_entry.get()
        if not message.strip():
            messagebox.showerror("Empty message", "Enter a message first.")
            return
        self.message_entry.delete(0, "end")
        node = self.node
        self._run_background(lambda: node.send_text(peer_id, message))

    def _send_file(self):
        if not self.node:
            return
        try:
            peer_id = self._selected_peer()
        except ValueError as exc:
            messagebox.showerror("No peer selected", str(exc))
            return
        path = filedialog.askopenfilename(title="Choose a file to send")
        if path:
            self._log(f"Sending {Path(path).name}...")
            node = self.node
            self._run_background(lambda: node.send_file(peer_id, path))

    def _refresh_peers(self):
        selected_id = None
        selection = self.peer_list.curselection()
        if selection and selection[0] < len(self.peer_ids):
            selected_id = self.peer_ids[selection[0]]
        peers = self.node.peers() if self.node else []
        self.peer_list.delete(0, "end")
        self.peer_ids = []
        for peer_id, name, address, port in peers:
            self.peer_ids.append(peer_id)
            self.peer_list.insert("end", f"{name} [{peer_id}]  {address}:{port}")
        if selected_id in self.peer_ids:
            self.peer_list.selection_set(self.peer_ids.index(selected_id))
        self._update_send_buttons()

    def _update_send_buttons(self):
        state = "normal" if self.node and self.peer_list.curselection() else "disabled"
        self.send_button.configure(state=state)
        self.file_button.configure(state=state)

    def _process_events(self):
        try:
            while True:
                kind, info = self.events.get_nowait()
                if kind == "started":
                    self._log(f"Started {info['name']} [{info['peer_id']}] on port {info['port']}.")
                elif kind == "stopped":
                    self._log("Peer stopped.")
                elif kind == "connected":
                    self._log(f"Connected to {info['name']} [{info['peer_id']}] at "
                              f"{info['address']}:{info['port']}.")
                    self._refresh_peers()
                elif kind == "disconnected":
                    self._log(f"{info['name']} disconnected.")
                    self._refresh_peers()
                elif kind == "text":
                    self._log(f"{info['name']} -> You: {info['message']}")
                elif kind == "text_sent":
                    self._log(f"You -> {info['name']}: {info['message']}")
                elif kind == "file_received":
                    self._log(f"Received {info['filename']} ({info['size']} bytes) "
                              f"from {info['name']} in downloads/.")
                elif kind == "file_sent":
                    self._log(f"Sent {info['filename']} ({info['size']} bytes) "
                              f"to {info['name']}.")
                elif kind in ("error", "ui_error"):
                    self._log(f"[ERROR] {info['message']}")
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    PeerApp(root)
    root.mainloop()
