import tkinter as tk
from tkinter import ttk
import importlib.metadata
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGES_DIR = os.path.join(BASE_DIR, "packages")


def get_installed_packages():
    """Retourne un dict {nom_normalisé: version} des packages installés."""
    result = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        result[_normalize(name)] = dist.metadata["Version"]
    return result


def get_local_whls():
    """Retourne la liste des (nom, version, filename) trouvés dans packages/."""
    if not os.path.isdir(PACKAGES_DIR):
        os.makedirs(PACKAGES_DIR)
    items = []
    for f in sorted(os.listdir(PACKAGES_DIR)):
        if f.endswith(".whl"):
            name, version = _parse_whl(f)
            items.append((name, version, f))
    return items


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_whl(filename):
    """Extrait nom et version depuis le nom de fichier wheel."""
    parts = filename.split("-")
    name = parts[0].replace("_", "-") if len(parts) >= 1 else filename
    version = parts[1] if len(parts) >= 2 else "?"
    return name, version


class PackageManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestionnaire de Packages Python")
        self.geometry("1000x680")
        self.resizable(True, True)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # En-tête
        header = tk.Frame(self, bg="#2c3e50", pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="Gestionnaire de Packages Python",
            font=("Helvetica", 14, "bold"), fg="white", bg="#2c3e50"
        ).pack()

        # Barre d'outils
        toolbar = tk.Frame(self, pady=4)
        toolbar.pack(fill=tk.X, padx=10)
        tk.Label(toolbar, text="Rechercher :").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        tk.Entry(toolbar, textvariable=self.search_var, width=30).pack(side=tk.LEFT, padx=6)
        tk.Button(toolbar, text="Rafraîchir", command=self.refresh).pack(side=tk.LEFT, padx=4)

        # Légende
        legend = tk.Frame(toolbar)
        legend.pack(side=tk.RIGHT)
        for color, label in [("#27ae60", "Installé (même version)"),
                              ("#e67e22", "Installé (version différente)"),
                              ("#e74c3c", "Non installé")]:
            tk.Label(legend, text="■", fg=color, font=("Helvetica", 12)).pack(side=tk.LEFT)
            tk.Label(legend, text=label, font=("Helvetica", 8)).pack(side=tk.LEFT, padx=(0, 8))

        # Panneau à deux colonnes
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=6)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        # --- Colonne gauche : installés ---
        left = tk.LabelFrame(paned, text="Packages installés sur le poste", padx=4, pady=4)
        paned.add(left, stretch="always")

        self.installed_tree = self._make_tree(left, ("Nom", "Version installée"))
        self.installed_count = tk.StringVar()
        tk.Label(left, textvariable=self.installed_count, anchor="w", fg="#555").pack(fill=tk.X)

        # --- Colonne droite : comparaison packages/ ---
        right = tk.LabelFrame(paned, text="Packages disponibles dans packages/", padx=4, pady=4)
        paned.add(right, stretch="always")

        self.cmp_tree = self._make_tree(
            right, ("Fichier .whl", "Version dispo", "Version installée", "Statut")
        )
        self.cmp_tree.column("Fichier .whl", width=260)
        self.cmp_tree.column("Version dispo", width=110)
        self.cmp_tree.column("Version installée", width=120)
        self.cmp_tree.column("Statut", width=80)
        self.cmp_count = tk.StringVar()
        tk.Label(right, textvariable=self.cmp_count, anchor="w", fg="#555").pack(fill=tk.X)

        # Résumé bas de page
        self.summary_var = tk.StringVar()
        tk.Label(self, textvariable=self.summary_var, anchor="w", fg="#333",
                 font=("Helvetica", 9)).pack(fill=tk.X, padx=10, pady=(0, 6))

        self._installed = {}
        self._whls = []

    def _make_tree(self, parent, columns):
        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        cols = tuple(columns)
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=140)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def refresh(self):
        self._installed = get_installed_packages()
        self._whls = get_local_whls()
        self._apply_filter()

    def _apply_filter(self):
        query = self.search_var.get().lower()

        # Liste installés
        self.installed_tree.delete(*self.installed_tree.get_children())
        filtered_installed = [
            (n, v) for n, v in sorted(self._installed.items()) if query in n
        ]
        for name, version in filtered_installed:
            self.installed_tree.insert("", tk.END, values=(name, version))
        self.installed_count.set(f"{len(filtered_installed)} package(s) affiché(s)")

        # Comparaison
        self.cmp_tree.delete(*self.cmp_tree.get_children())
        ok = diff = missing = 0
        for name, version_dispo, filename in self._whls:
            if query and query not in name.lower() and query not in filename.lower():
                continue
            installed_version = self._installed.get(_normalize(name))
            if installed_version is None:
                statut = "Absent"
                color = "#e74c3c"
                installed_version = "—"
                missing += 1
            elif _normalize(installed_version) == _normalize(version_dispo):
                statut = "OK"
                color = "#27ae60"
                ok += 1
            else:
                statut = "Différent"
                color = "#e67e22"
                diff += 1
            iid = self.cmp_tree.insert(
                "", tk.END,
                values=(filename, version_dispo, installed_version, statut)
            )
            self.cmp_tree.tag_configure(color, foreground=color)
            self.cmp_tree.item(iid, tags=(color,))

        total = len(self._whls)
        self.cmp_count.set(f"{total} fichier(s) .whl dans packages/")
        self.summary_var.set(
            f"Résumé packages/ :  {ok} OK  |  {diff} version(s) différente(s)  |  {missing} non installé(s)"
        )


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


if __name__ == "__main__":
    app = PackageManagerApp()
    app.mainloop()
