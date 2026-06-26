import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import threading
import importlib.metadata
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGES_DIR = os.path.join(BASE_DIR, "packages")


def get_installed_packages():
    packages = sorted(
        importlib.metadata.distributions(),
        key=lambda d: d.metadata["Name"].lower()
    )
    return [(d.metadata["Name"], d.metadata["Version"]) for d in packages]


def get_local_whls():
    if not os.path.isdir(PACKAGES_DIR):
        os.makedirs(PACKAGES_DIR)
    return sorted(f for f in os.listdir(PACKAGES_DIR) if f.endswith(".whl"))


class PackageManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestionnaire de Packages Python")
        self.geometry("900x650")
        self.resizable(True, True)
        self._build_ui()
        self._load_packages()
        self._load_whls()

    def _build_ui(self):
        header = tk.Frame(self, bg="#2c3e50", pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="Gestionnaire de Packages Python",
            font=("Helvetica", 14, "bold"), fg="white", bg="#2c3e50"
        ).pack()

        # Panneau principal : deux colonnes
        main = tk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        # --- Colonne gauche : packages installés ---
        left = tk.LabelFrame(main, text="Packages installés", padx=6, pady=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        search_frame = tk.Frame(left)
        search_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Label(search_frame, text="Rechercher :").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_packages())
        tk.Entry(search_frame, textvariable=self.search_var, width=25).pack(side=tk.LEFT, padx=4)
        tk.Button(search_frame, text="Rafraîchir", command=self._load_packages).pack(side=tk.RIGHT)

        tree_frame = tk.Frame(left)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        columns = ("name", "version")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Nom", command=lambda: self._sort("name"))
        self.tree.heading("version", text="Version", command=lambda: self._sort("version"))
        self.tree.column("name", width=300)
        self.tree.column("version", width=120)
        sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.count_var = tk.StringVar(value="")
        tk.Label(left, textvariable=self.count_var, anchor="w", fg="#555").grid(
            row=2, column=0, sticky="w", pady=(2, 0)
        )

        # --- Colonne droite : .whl disponibles ---
        right = tk.LabelFrame(main, text=f"Packages disponibles  (dossier : packages/)", padx=6, pady=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        btn_frame = tk.Frame(right)
        btn_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Button(btn_frame, text="Rafraîchir la liste", command=self._load_whls).pack(side=tk.LEFT)
        tk.Label(btn_frame, text="← Déposez vos .whl ici", fg="#888", font=("Helvetica", 8)).pack(side=tk.RIGHT)

        whl_frame = tk.Frame(right)
        whl_frame.grid(row=1, column=0, sticky="nsew")
        self.whl_list = tk.Listbox(whl_frame, selectmode=tk.SINGLE, activestyle="dotbox")
        whl_sb = ttk.Scrollbar(whl_frame, orient=tk.VERTICAL, command=self.whl_list.yview)
        self.whl_list.configure(yscrollcommand=whl_sb.set)
        self.whl_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        whl_sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.whl_count_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.whl_count_var, anchor="w", fg="#555").grid(
            row=2, column=0, sticky="w", pady=(2, 0)
        )

        tk.Button(
            right, text="Installer le package sélectionné",
            bg="#27ae60", fg="white", font=("Helvetica", 10, "bold"),
            command=self._install_selected
        ).grid(row=3, column=0, sticky="ew", pady=(6, 0))

        # --- Journal ---
        log_frame = tk.LabelFrame(self, text="Journal", padx=6, pady=4)
        log_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

        self.log_text = tk.Text(
            log_frame, height=6, state=tk.DISABLED,
            bg="#1e1e1e", fg="#d4d4d4", font=("Courier", 9)
        )
        log_sb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._all_packages = []
        self._sort_col = "name"
        self._sort_reverse = False

    # ── Packages installés ──────────────────────────────────────────────────

    def _load_packages(self):
        self._log("Chargement des packages installés…")
        self._all_packages = get_installed_packages()
        self._filter_packages()
        self._log(f"{len(self._all_packages)} packages trouvés.")

    def _filter_packages(self):
        query = self.search_var.get().lower()
        filtered = [(n, v) for n, v in self._all_packages if query in n.lower()]
        self._populate_tree(filtered)

    def _populate_tree(self, packages):
        self.tree.delete(*self.tree.get_children())
        for name, version in packages:
            self.tree.insert("", tk.END, values=(name, version))
        self.count_var.set(f"{len(packages)} package(s) affiché(s)")

    def _sort(self, col):
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        idx = 0 if col == "name" else 1
        query = self.search_var.get().lower()
        filtered = [(n, v) for n, v in self._all_packages if query in n.lower()]
        filtered.sort(key=lambda x: x[idx].lower(), reverse=self._sort_reverse)
        self._populate_tree(filtered)

    # ── Fichiers .whl locaux ────────────────────────────────────────────────

    def _load_whls(self):
        self.whl_list.delete(0, tk.END)
        whls = get_local_whls()
        for whl in whls:
            self.whl_list.insert(tk.END, whl)
        count = len(whls)
        self.whl_count_var.set(f"{count} fichier(s) disponible(s)")
        if count == 0:
            self._log(f"Aucun .whl trouvé dans {PACKAGES_DIR}")
        else:
            self._log(f"{count} fichier(s) .whl disponible(s) dans packages/")

    def _install_selected(self):
        selection = self.whl_list.curselection()
        if not selection:
            messagebox.showwarning("Rien de sélectionné", "Sélectionnez un fichier .whl dans la liste.")
            return
        filename = self.whl_list.get(selection[0])
        path = os.path.join(PACKAGES_DIR, filename)
        self._log(f"Installation de : {filename}")
        threading.Thread(target=self._run_install, args=(path,), daemon=True).start()

    def _run_install(self, path):
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "--no-index", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in process.stdout:
                self._log(line.rstrip())
            process.wait()
            if process.returncode == 0:
                self._log("Installation réussie.")
                self.after(0, self._load_packages)
            else:
                self._log(f"Echec de l'installation (code {process.returncode}).")
        except Exception as e:
            self._log(f"Exception : {e}")

    # ── Journal ─────────────────────────────────────────────────────────────

    def _log(self, message):
        def _append():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.after(0, _append)


if __name__ == "__main__":
    app = PackageManagerApp()
    app.mainloop()
