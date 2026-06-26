import tkinter as tk
from tkinter import ttk, messagebox
import importlib.metadata
import os
import re
import subprocess
import sys
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGES_DIR = os.path.join(BASE_DIR, "packages")
USERS_DIR = r"C:\Users"


# ── Utilitaires ─────────────────────────────────────────────────────────────

def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_whl(filename):
    parts = filename.split("-")
    name = parts[0].replace("_", "-") if parts else filename
    version = parts[1] if len(parts) >= 2 else "?"
    return name, version


def get_local_whls():
    if not os.path.isdir(PACKAGES_DIR):
        os.makedirs(PACKAGES_DIR)
    items = []
    for f in sorted(os.listdir(PACKAGES_DIR)):
        if f.endswith(".whl"):
            name, version = _parse_whl(f)
            items.append((name, version, f))
    return items


def get_system_packages():
    result = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        result[_normalize(name)] = dist.metadata["Version"]
    return result


def find_venvs(base=USERS_DIR):
    """Scan récursif de base pour trouver les dossiers contenant pyvenv.cfg."""
    venvs = []
    try:
        for root, dirs, files in os.walk(base):
            if "pyvenv.cfg" in files:
                cfg_path = os.path.join(root, "pyvenv.cfg")
                info = _read_pyvenv_cfg(cfg_path)
                pip_exe = os.path.join(root, "Scripts", "pip.exe")
                python_exe = os.path.join(root, "Scripts", "python.exe")
                if os.path.isfile(python_exe):
                    venvs.append({
                        "path": root,
                        "name": os.path.basename(root),
                        "python_version": info.get("version", "?"),
                        "system_site": info.get("include-system-site-packages", "false").lower() == "true",
                        "pip_exe": pip_exe if os.path.isfile(pip_exe) else None,
                        "python_exe": python_exe,
                    })
                # Ne pas descendre dans le venv lui-même
                dirs[:] = [d for d in dirs if d not in {"Lib", "Scripts", "Include", "lib", "bin", "include"}]
    except PermissionError:
        pass
    return venvs


def _read_pyvenv_cfg(path):
    info = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "=" in line:
                    key, _, val = line.partition("=")
                    info[key.strip().lower()] = val.strip()
    except OSError:
        pass
    return info


def get_venv_packages(pip_exe):
    """Retourne {nom_normalisé: version} via pip list du venv."""
    result = {}
    try:
        out = subprocess.check_output(
            [pip_exe, "list", "--format=freeze"],
            stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            if "==" in line:
                name, _, version = line.partition("==")
                result[_normalize(name.strip())] = version.strip()
    except Exception:
        pass
    return result


# ── Widget réutilisable : tableau comparaison + installation ─────────────────

class ComparePanel(tk.Frame):
    """Panneau comparaison packages installés vs .whl disponibles."""

    def __init__(self, parent, log_fn, **kwargs):
        super().__init__(parent, **kwargs)
        self._log = log_fn
        self._whls = []
        self._installed = {}
        self._pip_exe = None
        self._build()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Barre recherche + légende
        top = tk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Label(top, text="Rechercher :").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_display())
        tk.Entry(top, textvariable=self.search_var, width=25).pack(side=tk.LEFT, padx=4)

        legend = tk.Frame(top)
        legend.pack(side=tk.RIGHT)
        for color, label in [
            ("#27ae60", "OK"),
            ("#e67e22", "Différent"),
            ("#e74c3c", "Absent"),
        ]:
            tk.Label(legend, text="■", fg=color, font=("Helvetica", 11)).pack(side=tk.LEFT)
            tk.Label(legend, text=label, font=("Helvetica", 8)).pack(side=tk.LEFT, padx=(0, 6))

        # Tableau principal
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=5)
        paned.grid(row=1, column=0, sticky="nsew")

        # Installés
        left = tk.LabelFrame(paned, text="Packages installés", padx=4, pady=4)
        paned.add(left, stretch="always")
        self.inst_tree = self._make_tree(left, ("Nom", "Version"))
        self.inst_count = tk.StringVar()
        tk.Label(left, textvariable=self.inst_count, anchor="w", fg="#555").pack(fill=tk.X)

        # Disponibles + actions
        right = tk.LabelFrame(paned, text="Packages disponibles dans packages/", padx=4, pady=4)
        paned.add(right, stretch="always")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.cmp_tree = self._make_tree(
            right, ("Fichier .whl", "Dispo", "Installé", "Statut"),
            extra_col_widths={"Fichier .whl": 220, "Dispo": 90, "Installé": 90, "Statut": 75}
        )
        self.cmp_count = tk.StringVar()
        tk.Label(right, textvariable=self.cmp_count, anchor="w", fg="#555").pack(fill=tk.X)

        btn_frame = tk.Frame(right)
        btn_frame.pack(fill=tk.X, pady=(4, 0))
        tk.Button(btn_frame, text="Installer / Mettre à jour le sélectionné",
                  bg="#2980b9", fg="white", font=("Helvetica", 9, "bold"),
                  command=self._install_selected).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frame, text="Installer tous les absents/différents",
                  bg="#8e44ad", fg="white", font=("Helvetica", 9, "bold"),
                  command=self._install_all_missing).pack(side=tk.LEFT)

        # Résumé
        self.summary_var = tk.StringVar()
        tk.Label(self, textvariable=self.summary_var, anchor="w",
                 fg="#333", font=("Helvetica", 9)).grid(row=2, column=0, sticky="w", pady=(2, 0))

    def _make_tree(self, parent, columns, extra_col_widths=None):
        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=tuple(columns), show="headings", selectmode="browse")
        for col in columns:
            tree.heading(col, text=col)
            w = (extra_col_widths or {}).get(col, 130)
            tree.column(col, width=w)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def load(self, installed: dict, whls: list, pip_exe=None):
        self._installed = installed
        self._whls = whls
        self._pip_exe = pip_exe
        self.refresh_display()

    def refresh_display(self):
        query = self.search_var.get().lower()

        # Installés
        self.inst_tree.delete(*self.inst_tree.get_children())
        filtered = [(n, v) for n, v in sorted(self._installed.items()) if query in n]
        for name, version in filtered:
            self.inst_tree.insert("", tk.END, values=(name, version))
        self.inst_count.set(f"{len(filtered)} package(s)")

        # Comparaison
        self.cmp_tree.delete(*self.cmp_tree.get_children())
        ok = diff = missing = 0
        for name, ver_dispo, filename in self._whls:
            if query and query not in name.lower() and query not in filename.lower():
                continue
            inst_ver = self._installed.get(_normalize(name))
            if inst_ver is None:
                statut, color, inst_ver = "Absent", "#e74c3c", "—"
                missing += 1
            elif _normalize(inst_ver) == _normalize(ver_dispo):
                statut, color = "OK", "#27ae60"
                ok += 1
            else:
                statut, color = "Différent", "#e67e22"
                diff += 1
            iid = self.cmp_tree.insert("", tk.END,
                                       values=(filename, ver_dispo, inst_ver, statut))
            self.cmp_tree.tag_configure(color, foreground=color)
            self.cmp_tree.item(iid, tags=(color,))

        self.cmp_count.set(f"{len(self._whls)} fichier(s) .whl")
        self.summary_var.set(
            f"Résumé :  {ok} OK  |  {diff} version(s) différente(s)  |  {missing} absent(s)"
        )

    def _get_whl_path(self, filename):
        return os.path.join(PACKAGES_DIR, filename)

    def _install_selected(self):
        sel = self.cmp_tree.selection()
        if not sel:
            messagebox.showwarning("Rien de sélectionné",
                                   "Sélectionnez un fichier .whl dans la liste de droite.")
            return
        filename = self.cmp_tree.item(sel[0])["values"][0]
        statut = self.cmp_tree.item(sel[0])["values"][3]
        if statut == "OK":
            if not messagebox.askyesno("Déjà installé",
                                       "Ce package est déjà à jour. Réinstaller quand même ?"):
                return
        self._run_install([self._get_whl_path(filename)])

    def _install_all_missing(self):
        paths = []
        for iid in self.cmp_tree.get_children():
            vals = self.cmp_tree.item(iid)["values"]
            if vals[3] in ("Absent", "Différent"):
                paths.append(self._get_whl_path(vals[0]))
        if not paths:
            messagebox.showinfo("Rien à faire", "Tous les packages sont déjà à jour.")
            return
        if not messagebox.askyesno("Confirmation",
                                   f"Installer / mettre à jour {len(paths)} package(s) ?"):
            return
        self._run_install(paths)

    def _run_install(self, paths):
        if not self._pip_exe:
            messagebox.showerror("Pip introuvable",
                                 "Impossible de trouver pip pour cet environnement.")
            return
        threading.Thread(target=self._do_install, args=(paths,), daemon=True).start()

    def _do_install(self, paths):
        for path in paths:
            self._log(f"→ Installation : {os.path.basename(path)}")
            try:
                proc = subprocess.Popen(
                    [self._pip_exe, "install", "--no-index", path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                for line in proc.stdout:
                    self._log(line.rstrip())
                proc.wait()
                if proc.returncode == 0:
                    self._log(f"✓ {os.path.basename(path)} installé.")
                else:
                    self._log(f"✗ Erreur pour {os.path.basename(path)} (code {proc.returncode}).")
            except Exception as e:
                self._log(f"✗ Exception : {e}")
        self._log("— Terminé —")
        # Recharger les packages après installation
        self.after(0, self._reload_after_install)

    def _reload_after_install(self):
        if self._pip_exe == sys.executable.replace("python.exe", "pip.exe") or \
                self._pip_exe is None:
            new_installed = get_system_packages()
        else:
            new_installed = get_venv_packages(self._pip_exe)
        self.load(new_installed, self._whls, self._pip_exe)


# ── Application principale ───────────────────────────────────────────────────

class PackageManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestionnaire de Packages Python")
        self.geometry("1100x720")
        self.resizable(True, True)
        self._whls = []
        self._build_ui()
        self._load_all()

    def _build_ui(self):
        # En-tête
        header = tk.Frame(self, bg="#2c3e50", pady=8)
        header.pack(fill=tk.X)
        tk.Label(header, text="Gestionnaire de Packages Python",
                 font=("Helvetica", 14, "bold"), fg="white", bg="#2c3e50").pack()

        # Onglets
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # Onglet 1 — Python système
        tab_sys = tk.Frame(self.notebook)
        self.notebook.add(tab_sys, text="  Python système  ")
        tab_sys.rowconfigure(0, weight=1)
        tab_sys.columnconfigure(0, weight=1)

        sys_top = tk.Frame(tab_sys)
        sys_top.pack(fill=tk.X, padx=6, pady=(6, 2))
        tk.Button(sys_top, text="Rafraîchir", command=self._reload_system).pack(side=tk.LEFT)
        self.sys_info = tk.StringVar()
        tk.Label(sys_top, textvariable=self.sys_info, fg="#555").pack(side=tk.LEFT, padx=10)

        self.sys_panel = ComparePanel(tab_sys, self._log)
        self.sys_panel.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Onglet 2 — Environnements virtuels
        tab_venv = tk.Frame(self.notebook)
        self.notebook.add(tab_venv, text="  Environnements virtuels  ")

        venv_top = tk.Frame(tab_venv)
        venv_top.pack(fill=tk.X, padx=6, pady=(6, 2))
        tk.Button(venv_top, text="Scanner C:\\Users\\",
                  command=self._scan_venvs).pack(side=tk.LEFT)
        self.venv_status = tk.StringVar(value="Cliquez sur Scanner pour détecter les venvs.")
        tk.Label(venv_top, textvariable=self.venv_status, fg="#555").pack(side=tk.LEFT, padx=10)

        # Liste des venvs
        venv_list_frame = tk.LabelFrame(tab_venv, text="Venvs détectés", padx=4, pady=4)
        venv_list_frame.pack(fill=tk.X, padx=6, pady=(0, 4))

        cols = ("Nom", "Chemin", "Python", "Héritage global")
        self.venv_tree = ttk.Treeview(venv_list_frame, columns=cols,
                                      show="headings", height=5, selectmode="browse")
        self.venv_tree.heading("Nom", text="Nom")
        self.venv_tree.heading("Chemin", text="Chemin")
        self.venv_tree.heading("Python", text="Python")
        self.venv_tree.heading("Héritage global", text="Héritage global")
        self.venv_tree.column("Nom", width=180)
        self.venv_tree.column("Chemin", width=420)
        self.venv_tree.column("Python", width=80)
        self.venv_tree.column("Héritage global", width=120)
        venv_sb = ttk.Scrollbar(venv_list_frame, orient=tk.VERTICAL, command=self.venv_tree.yview)
        self.venv_tree.configure(yscrollcommand=venv_sb.set)
        self.venv_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        venv_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.venv_tree.bind("<<TreeviewSelect>>", self._on_venv_select)

        # Panneau comparaison du venv sélectionné
        self.venv_panel = ComparePanel(tab_venv, self._log)
        self.venv_panel.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Journal commun
        log_frame = tk.LabelFrame(self, text="Journal", padx=6, pady=4)
        log_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.log_text = tk.Text(log_frame, height=5, state=tk.DISABLED,
                                bg="#1e1e1e", fg="#d4d4d4", font=("Courier", 9))
        log_sb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._venvs = []

    # ── Chargement ────────────────────────────────────────────────────────────

    def _load_all(self):
        self._whls = get_local_whls()
        self._reload_system()

    def _reload_system(self):
        self._log("Chargement des packages système…")
        installed = get_system_packages()
        pip_exe = os.path.join(os.path.dirname(sys.executable), "pip.exe")
        if not os.path.isfile(pip_exe):
            pip_exe = sys.executable  # fallback : python -m pip géré ailleurs
        self.sys_panel.load(installed, self._whls, pip_exe)
        self.sys_info.set(f"Python {sys.version.split()[0]}  —  {len(installed)} packages")
        self._log(f"Système : {len(installed)} packages, {len(self._whls)} .whl disponibles.")

    def _scan_venvs(self):
        self.venv_status.set("Scan en cours…")
        self.venv_tree.delete(*self.venv_tree.get_children())
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        venvs = find_venvs(USERS_DIR)
        self._venvs = venvs
        self.after(0, lambda: self._populate_venvs(venvs))

    def _populate_venvs(self, venvs):
        self.venv_tree.delete(*self.venv_tree.get_children())
        for v in venvs:
            heritage = "Oui (hérite global)" if v["system_site"] else "Non (isolé)"
            self.venv_tree.insert("", tk.END, iid=v["path"],
                                  values=(v["name"], v["path"],
                                          v["python_version"], heritage))
        self.venv_status.set(f"{len(venvs)} venv(s) détecté(s) dans C:\\Users\\")
        self._log(f"Scan terminé : {len(venvs)} venv(s) trouvé(s).")

    def _on_venv_select(self, _event):
        sel = self.venv_tree.selection()
        if not sel:
            return
        path = sel[0]
        venv = next((v for v in self._venvs if v["path"] == path), None)
        if not venv:
            return
        self._log(f"Chargement des packages de : {venv['name']}…")
        threading.Thread(target=self._load_venv_packages, args=(venv,), daemon=True).start()

    def _load_venv_packages(self, venv):
        if not venv["pip_exe"]:
            self.after(0, lambda: self._log("pip introuvable dans ce venv."))
            return
        installed = get_venv_packages(venv["pip_exe"])
        self.after(0, lambda: self.venv_panel.load(installed, self._whls, venv["pip_exe"]))
        self.after(0, lambda: self._log(
            f"{venv['name']} : {len(installed)} packages — "
            f"{'hérite du global' if venv['system_site'] else 'isolé'}"
        ))

    # ── Journal ───────────────────────────────────────────────────────────────

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
