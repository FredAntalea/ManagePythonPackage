import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import sys
import threading
import importlib.metadata


def get_installed_packages():
    packages = sorted(
        importlib.metadata.distributions(),
        key=lambda d: d.metadata["Name"].lower()
    )
    return [(d.metadata["Name"], d.metadata["Version"]) for d in packages]


class PackageManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestionnaire de Packages Python")
        self.geometry("800x600")
        self.resizable(True, True)
        self._build_ui()
        self._load_packages()

    def _build_ui(self):
        # Titre
        header = tk.Frame(self, bg="#2c3e50", pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="Gestionnaire de Packages Python",
            font=("Helvetica", 14, "bold"), fg="white", bg="#2c3e50"
        ).pack()

        # Zone de recherche
        search_frame = tk.Frame(self, pady=6)
        search_frame.pack(fill=tk.X, padx=10)
        tk.Label(search_frame, text="Rechercher :").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_packages())
        tk.Entry(search_frame, textvariable=self.search_var, width=40).pack(side=tk.LEFT, padx=6)
        tk.Button(search_frame, text="Rafraîchir", command=self._load_packages).pack(side=tk.RIGHT)

        # Liste des packages
        list_frame = tk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))

        columns = ("name", "version")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Nom du package", command=lambda: self._sort("name"))
        self.tree.heading("version", text="Version", command=lambda: self._sort("version"))
        self.tree.column("name", width=450)
        self.tree.column("version", width=150)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Compteur
        self.count_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.count_var, anchor="w").pack(fill=tk.X, padx=10)

        # Séparateur
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=4)

        # Installation locale .whl
        install_frame = tk.LabelFrame(self, text="Installer un package local (.whl)", padx=8, pady=6)
        install_frame.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.whl_path_var = tk.StringVar()
        tk.Entry(install_frame, textvariable=self.whl_path_var, width=60).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(install_frame, text="Parcourir…", command=self._browse_whl).pack(side=tk.LEFT)
        tk.Button(
            install_frame, text="Installer", bg="#27ae60", fg="white",
            command=self._install_whl
        ).pack(side=tk.LEFT, padx=6)

        # Journal
        log_frame = tk.LabelFrame(self, text="Journal", padx=6, pady=4)
        log_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 8))

        self.log_text = tk.Text(log_frame, height=6, state=tk.DISABLED, bg="#1e1e1e", fg="#d4d4d4",
                                font=("Courier", 9))
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._all_packages = []
        self._sort_col = "name"
        self._sort_reverse = False

    def _load_packages(self):
        self._log("Chargement des packages installés…")
        self._all_packages = get_installed_packages()
        self._filter_packages()
        self._log(f"{len(self._all_packages)} packages trouvés.")

    def _filter_packages(self):
        query = self.search_var.get().lower()
        filtered = [
            (n, v) for n, v in self._all_packages
            if query in n.lower()
        ]
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
        filtered = [
            (n, v) for n, v in self._all_packages
            if query in n.lower()
        ]
        filtered.sort(key=lambda x: x[idx].lower(), reverse=self._sort_reverse)
        self._populate_tree(filtered)

    def _browse_whl(self):
        path = filedialog.askopenfilename(
            title="Sélectionner un fichier .whl",
            filetypes=[("Wheel packages", "*.whl"), ("Tous les fichiers", "*.*")]
        )
        if path:
            self.whl_path_var.set(path)

    def _install_whl(self):
        path = self.whl_path_var.get().strip()
        if not path:
            messagebox.showwarning("Chemin manquant", "Veuillez sélectionner un fichier .whl.")
            return
        if not path.endswith(".whl"):
            messagebox.showwarning("Fichier invalide", "Le fichier doit avoir l'extension .whl")
            return

        self._log(f"Installation de : {path}")
        threading.Thread(target=self._run_install, args=(path,), daemon=True).start()

    def _run_install(self, path):
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", path],
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
                self._log(f"Erreur lors de l'installation (code {process.returncode}).")
        except Exception as e:
            self._log(f"Exception : {e}")

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
