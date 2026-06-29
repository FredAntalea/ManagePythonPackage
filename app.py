import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import importlib.metadata
import os
import re
import subprocess
import sys
import threading
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGES_DIR = os.path.join(BASE_DIR, "packages")
USERS_DIR = r"C:\Users"


# ── Utilitaires ─────────────────────────────────────────────────────────────

def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_whl_requires(whl_path):
    """Lit les dépendances Requires-Dist depuis les métadonnées d'un .whl (zip)."""
    requires = []
    try:
        with zipfile.ZipFile(whl_path, "r") as zf:
            meta_files = [n for n in zf.namelist()
                          if n.endswith(".dist-info/METADATA") or n.endswith(".dist-info/WHEEL")]
            metadata_files = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
            if not metadata_files:
                return requires
            with zf.open(metadata_files[0]) as f:
                for raw in f.read().decode("utf-8", errors="ignore").splitlines():
                    if raw.startswith("Requires-Dist:"):
                        dep = raw[len("Requires-Dist:"):].strip()
                        # Ignorer les dépendances conditionnelles d'extras
                        if 'extra ==' in dep:
                            continue
                        # Extraire juste le nom du package
                        dep_name = re.split(r"[>=<!;\s\[]", dep)[0].strip()
                        if dep_name:
                            requires.append(_normalize(dep_name))
    except Exception:
        pass
    return requires


def resolve_install_order(paths):
    """Trie les chemins .whl dans l'ordre des dépendances (topological sort).
    Retourne (ordered_paths, warnings) où warnings liste les dépendances manquantes.
    """
    # Construire un index nom_normalisé -> path pour les wheels disponibles
    name_to_path = {}
    path_to_requires = {}
    for path in paths:
        filename = os.path.basename(path)
        name, *_ = _parse_whl(filename)
        norm = _normalize(name)
        name_to_path[norm] = path
        path_to_requires[path] = read_whl_requires(path)

    warnings = []
    visited = set()
    ordered = []

    def visit(path, stack=None):
        if stack is None:
            stack = set()
        if path in visited:
            return
        if path in stack:
            return  # cycle, on ignore
        stack.add(path)
        for dep_norm in path_to_requires.get(path, []):
            if dep_norm in name_to_path:
                visit(name_to_path[dep_norm], stack)
            # Si la dépendance n'est pas dans les paths à installer, on ne warn pas
            # (elle est peut-être déjà installée)
        visited.add(path)
        ordered.append(path)

    for path in paths:
        visit(path)

    return ordered, warnings


def _parse_whl(filename):
    parts = filename[:-4].split("-")
    name = parts[0].replace("_", "-") if parts else filename
    version = parts[1] if len(parts) >= 2 else "?"
    py_tag  = parts[2] if len(parts) >= 3 else ""
    abi_tag = parts[3] if len(parts) >= 4 else ""
    plat_tag = parts[4] if len(parts) >= 5 else ""
    return name, version, py_tag, abi_tag, plat_tag


def _check_wheel_compat(py_tag, abi_tag, plat_tag, target_python_version):
    if not target_python_version or target_python_version == "?":
        return True, ""
    if plat_tag == "any" and abi_tag == "none":
        return True, ""
    m = re.match(r"cp(\d)(\d+)", py_tag)
    if m:
        wheel_major, wheel_minor = int(m.group(1)), int(m.group(2))
        try:
            p = target_python_version.split(".")
            tgt_major, tgt_minor = int(p[0]), int(p[1])
        except (IndexError, ValueError):
            return True, ""
        if wheel_major != tgt_major or wheel_minor != tgt_minor:
            return False, f"Requiert Python {wheel_major}.{wheel_minor}, cible {tgt_major}.{tgt_minor}"
    if plat_tag and plat_tag != "any":
        import platform as _plat
        if "win" in plat_tag and sys.platform != "win32":
            return False, "Wheel Windows sur système non-Windows"
        if "win32" in plat_tag and _plat.machine().endswith("64"):
            return False, "Wheel 32-bit sur système 64-bit"
    return True, ""


def get_local_whls(target_python_version=""):
    if not os.path.isdir(PACKAGES_DIR):
        os.makedirs(PACKAGES_DIR)
    # First pass: index all wheels with compat
    all_whls = {}  # normalized_name -> (name, version, filename, compat, reason)
    for f in sorted(os.listdir(PACKAGES_DIR)):
        if f.endswith(".whl"):
            name, version, py_tag, abi_tag, plat_tag = _parse_whl(f)
            compat, reason = _check_wheel_compat(py_tag, abi_tag, plat_tag, target_python_version)
            all_whls[_normalize(name)] = (name, version, f, compat, reason)
    # Second pass: add deps_in_packages for each wheel
    items = []
    for norm_name, (name, version, filename, compat, reason) in all_whls.items():
        whl_path = os.path.join(PACKAGES_DIR, filename)
        deps = read_whl_requires(whl_path)
        deps_in_packages = [(dep, all_whls[dep][3]) for dep in deps if dep in all_whls]
        items.append((name, version, filename, compat, reason, deps_in_packages))
    items.sort(key=lambda x: x[2])
    return items


def get_system_packages():
    result = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        result[_normalize(name)] = dist.metadata["Version"]
    return result


def _get_system_packages_for(py_tag):
    """Retourne les packages installés pour une version Python système via py -X.Y -m pip list."""
    result = {}
    try:
        out = subprocess.check_output(
            ["py", py_tag, "-m", "pip", "list", "--format=freeze"],
            stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            if "==" in line:
                name, _, version = line.partition("==")
                result[_normalize(name.strip())] = version.strip()
    except Exception:
        pass
    return result


def get_venv_packages(pip_exe):
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


def find_venvs(base=USERS_DIR):
    venvs = []
    try:
        for root, dirs, files in os.walk(base):
            if "pyvenv.cfg" in files:
                info = _read_pyvenv_cfg(os.path.join(root, "pyvenv.cfg"))
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
                dirs[:] = [d for d in dirs if d not in
                           {"Lib", "Scripts", "Include", "lib", "bin", "include"}]
    except PermissionError:
        pass
    return venvs


def _read_pyvenv_cfg(path):
    info = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "=" in line:
                    k, _, v = line.partition("=")
                    info[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return info


def detect_python_installs():
    """Détecte les versions Python disponibles via le Python Launcher Windows."""
    versions = []
    try:
        out = subprocess.check_output(
            ["py", "-0"], stderr=subprocess.STDOUT, text=True
        )
        for line in out.splitlines():
            line = line.strip().lstrip("*").strip()
            m = re.match(r"(-V:[\d.]+|[\d.]+)", line)
            if m:
                tag = line.split()[0].lstrip("*").strip()
                versions.append(tag)
    except Exception:
        pass
    # Fallback : Python courant
    if not versions:
        versions.append(f"-{sys.version_info.major}.{sys.version_info.minor}")
    return versions


# ── Panneau comparaison + installation ──────────────────────────────────────

class ComparePanel(tk.Frame):

    def __init__(self, parent, log_fn, **kwargs):
        super().__init__(parent, **kwargs)
        self._log = log_fn
        self._whls = []
        self._installed = {}
        self._pip_exe = None
        self._build()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)   # row 1 = tableau (expansible)
        # row 0 = recherche/légende fixe
        # row 2 = boutons fixes
        # row 3 = résumé fixe

        # Barre recherche + légende
        search_bar = tk.Frame(self)
        search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Label(search_bar, text="Rechercher :").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_display())
        tk.Entry(search_bar, textvariable=self.search_var, width=22).pack(side=tk.LEFT, padx=4)
        legend = tk.Frame(search_bar)
        legend.pack(side=tk.RIGHT)
        for color, label in [("#27ae60", "OK"), ("#e67e22", "Différent"),
                              ("#e74c3c", "Absent"), ("#999999", "Incompatible"),
                              ("#8e44ad", "Bloqué")]:
            tk.Label(legend, text="■", fg=color, font=("Helvetica", 11)).pack(side=tk.LEFT)
            tk.Label(legend, text=label, font=("Helvetica", 8)).pack(side=tk.LEFT, padx=(0, 5))

        # Panneau gauche/droite
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=5)
        paned.grid(row=1, column=0, sticky="nsew")

        left = tk.LabelFrame(paned, text="Packages installés", padx=4, pady=4)
        paned.add(left, stretch="always")
        self.inst_tree = self._make_tree(left, ("Nom", "Version"))
        self.inst_count = tk.StringVar()
        tk.Label(left, textvariable=self.inst_count, anchor="w", fg="#555").pack(fill=tk.X)

        right = tk.LabelFrame(paned, text="Packages disponibles dans packages/", padx=4, pady=4)
        paned.add(right, stretch="always")
        self.cmp_tree = self._make_tree(
            right,
            ("Fichier .whl", "Dispo", "Installé", "Statut", "Note", "Dépend de"),
            widths={"Fichier .whl": 180, "Dispo": 65, "Installé": 65, "Statut": 70,
                    "Note": 140, "Dépend de": 160}
        )
        self.cmp_count = tk.StringVar()
        tk.Label(right, textvariable=self.cmp_count, anchor="w", fg="#555").pack(fill=tk.X)

        # Boutons toujours visibles (row fixe sous le paned)
        btn_bar = tk.Frame(self, pady=4)
        btn_bar.grid(row=2, column=0, sticky="ew")
        tk.Button(btn_bar, text="Installer / MAJ le sélectionné",
                  bg="#2980b9", fg="white", font=("Helvetica", 9, "bold"),
                  command=self._install_selected).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btn_bar, text="Installer tous les absents / différents",
                  bg="#8e44ad", fg="white", font=("Helvetica", 9, "bold"),
                  command=self._install_all_missing).pack(side=tk.LEFT)

        self.summary_var = tk.StringVar()
        tk.Label(self, textvariable=self.summary_var, anchor="w",
                 fg="#333", font=("Helvetica", 9)).grid(row=3, column=0, sticky="w")

    def _make_tree(self, parent, columns, widths=None):
        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=tuple(columns), show="headings", selectmode="browse")
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=(widths or {}).get(col, 120))
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

    def _topo_sort_whls(self, whls):
        name_to_whl = {_normalize(w[0]): w for w in whls}
        visited = set()
        ordered = []

        def visit(norm_name):
            if norm_name in visited:
                return
            visited.add(norm_name)
            whl = name_to_whl.get(norm_name)
            if whl and len(whl) >= 6:
                for dep_name, _ in whl[5]:
                    visit(dep_name)
            if whl:
                ordered.append(whl)

        for norm_name in list(name_to_whl.keys()):
            visit(norm_name)
        return ordered

    def refresh_display(self):
        query = self.search_var.get().lower()

        self.inst_tree.delete(*self.inst_tree.get_children())
        filtered = [(n, v) for n, v in sorted(self._installed.items()) if query in n]
        for name, version in filtered:
            self.inst_tree.insert("", tk.END, values=(name, version))
        self.inst_count.set(f"{len(filtered)} package(s)")

        self.cmp_tree.delete(*self.cmp_tree.get_children())
        ok = diff = missing = incompat = blocked = 0

        for whl_data in self._topo_sort_whls(self._whls):
            name, ver_dispo, filename = whl_data[0], whl_data[1], whl_data[2]
            compat, compat_reason = whl_data[3], whl_data[4]
            deps_in_packages = whl_data[5] if len(whl_data) >= 6 else []

            if query and query not in name.lower() and query not in filename.lower():
                continue

            deps_str = ", ".join(
                f"{d} {'✓' if c else '✗'}" for d, c in deps_in_packages
            ) if deps_in_packages else ""

            has_blocked_dep = any(not c for _, c in deps_in_packages)

            if not compat:
                statut, color, inst_ver, note = "Incompatible", "#999999", "—", compat_reason
                incompat += 1
            elif has_blocked_dep:
                inst_ver = self._installed.get(_normalize(name)) or "—"
                statut, color, note = "Bloqué", "#8e44ad", "Dép. incompatible"
                blocked += 1
            else:
                inst_ver = self._installed.get(_normalize(name))
                note = ""
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
                                       values=(filename, ver_dispo, inst_ver, statut, note, deps_str))
            self.cmp_tree.tag_configure(color, foreground=color)
            self.cmp_tree.item(iid, tags=(color,))

        self.cmp_count.set(f"{len(self._whls)} fichier(s) .whl")
        self.summary_var.set(
            f"Résumé :  {ok} OK  |  {diff} différent(s)  |  {missing} absent(s)  |"
            f"  {incompat} incompatible(s)  |  {blocked} bloqué(s)"
        )

    def _install_selected(self):
        sel = self.cmp_tree.selection()
        if not sel:
            messagebox.showwarning("Rien de sélectionné", "Sélectionnez un fichier .whl.")
            return
        vals = self.cmp_tree.item(sel[0])["values"]
        filename, statut = vals[0], vals[3]
        if statut == "Incompatible":
            messagebox.showerror("Incompatible", f"Ce wheel n'est pas compatible :\n{vals[4]}")
            return
        if statut == "Bloqué":
            messagebox.showerror("Bloqué", f"Une dépendance est incompatible :\n{vals[5]}")
            return
        if statut == "OK":
            if not messagebox.askyesno("Déjà installé",
                                       "Ce package est déjà à jour. Réinstaller quand même ?"):
                return
        self._run_install([os.path.join(PACKAGES_DIR, filename)])

    def _install_all_missing(self):
        paths = []
        for iid in self.cmp_tree.get_children():
            vals = self.cmp_tree.item(iid)["values"]
            if vals[3] in ("Absent", "Différent"):
                paths.append(os.path.join(PACKAGES_DIR, vals[0]))
        if not paths:
            messagebox.showinfo("Rien à faire", "Tous les packages compatibles sont déjà à jour.")
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

    def _pip_cmd(self):
        """Retourne la commande pip sous forme de liste."""
        if isinstance(self._pip_exe, list):
            return self._pip_exe          # ex. ["py", "-V:3.14", "-m", "pip"]
        return [self._pip_exe]            # ex. ["C:\...\pip.exe"]

    def _do_install(self, paths):
        # Résoudre l'ordre des dépendances
        ordered, _ = resolve_install_order(paths)

        # Vérifier les dépendances manquantes dans packages/
        available_names = set()
        if os.path.isdir(PACKAGES_DIR):
            for f in os.listdir(PACKAGES_DIR):
                if f.endswith(".whl"):
                    n, *_ = _parse_whl(f)
                    available_names.add(_normalize(n))

        for path in ordered:
            deps = read_whl_requires(path)
            missing_deps = [d for d in deps
                            if d not in available_names and d not in
                            {_normalize(os.path.basename(p).split("-")[0].replace("_", "-"))
                             for p in ordered}]
            if missing_deps:
                self._log(f"⚠ Dépendances peut-être manquantes pour "
                          f"{os.path.basename(path)} : {', '.join(missing_deps)}")

            self._log(f"→ Installation : {os.path.basename(path)}")
            try:
                cmd = self._pip_cmd() + ["install", "--no-index", path]
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                for line in proc.stdout:
                    self._log(line.rstrip())
                proc.wait()
                if proc.returncode == 0:
                    self._log(f"✓ {os.path.basename(path)} installé.")
                else:
                    self._log(f"✗ Erreur (code {proc.returncode}).")
            except Exception as e:
                self._log(f"✗ Exception : {e}")
        self._log("— Terminé —")
        self.after(0, self._reload_after_install)

    def _reload_after_install(self):
        if isinstance(self._pip_exe, list):
            # commande système : ["py", "-V:3.13", "-m", "pip"]
            tag = self._pip_exe[1] if len(self._pip_exe) > 1 else ""
            new_installed = _get_system_packages_for(tag)
        elif self._pip_exe:
            new_installed = get_venv_packages(self._pip_exe)
        else:
            new_installed = get_system_packages()
        self.load(new_installed, self._whls, self._pip_exe)


# ── Application principale ───────────────────────────────────────────────────

class PackageManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestionnaire de Packages Python")
        self.geometry("1150x820")
        self.resizable(True, True)
        self._venvs = []
        self._build_ui()
        self._load_system()

    def _build_ui(self):
        header = tk.Frame(self, bg="#2c3e50", pady=8)
        header.pack(fill=tk.X)
        tk.Label(header, text="Gestionnaire de Packages Python",
                 font=("Helvetica", 14, "bold"), fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=12)
        self._log_btn = tk.Button(header, text="Journal",
                                  bg="#34495e", fg="white", font=("Helvetica", 9),
                                  relief=tk.FLAT, padx=8, command=self._toggle_journal)
        self._log_btn.pack(side=tk.RIGHT, padx=12)

        # Notebook plein écran
        nb_frame = tk.Frame(self)
        nb_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        nb_frame.rowconfigure(0, weight=1)
        nb_frame.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(nb_frame)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        # Journal (caché par défaut)
        self._log_visible = False
        self._log_frame = tk.LabelFrame(self, text="Journal", padx=6, pady=4)
        self._log_frame.rowconfigure(0, weight=1)
        self._log_frame.columnconfigure(0, weight=1)

        # Onglet 1 — Python système
        tab_sys = tk.Frame(self.notebook)
        self.notebook.add(tab_sys, text="  Python système  ")
        tab_sys.rowconfigure(1, weight=1)
        tab_sys.columnconfigure(0, weight=1)

        sys_bar = tk.Frame(tab_sys, pady=4)
        sys_bar.grid(row=0, column=0, sticky="ew", padx=6)
        tk.Label(sys_bar, text="Version Python :").pack(side=tk.LEFT)
        self.sys_py_var = tk.StringVar()
        self.sys_py_combo = ttk.Combobox(sys_bar, textvariable=self.sys_py_var,
                                         state="readonly", width=14)
        self.sys_py_combo.pack(side=tk.LEFT, padx=4)
        self.sys_py_combo.bind("<<ComboboxSelected>>", lambda _: self._load_system())
        tk.Button(sys_bar, text="Rafraîchir", command=self._load_system).pack(side=tk.LEFT, padx=4)
        self.sys_info = tk.StringVar()
        tk.Label(sys_bar, textvariable=self.sys_info, fg="#555").pack(side=tk.LEFT, padx=10)

        self.sys_panel = ComparePanel(tab_sys, self._log)
        self.sys_panel.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))

        # Onglet 2 — Environnements virtuels
        tab_venv = tk.Frame(self.notebook)
        self.notebook.add(tab_venv, text="  Environnements virtuels  ")
        tab_venv.rowconfigure(1, weight=1)
        tab_venv.columnconfigure(0, weight=1)

        venv_bar = tk.Frame(tab_venv, pady=4)
        venv_bar.grid(row=0, column=0, sticky="ew", padx=6)
        tk.Button(venv_bar, text="Scanner C:\\Users\\",
                  command=self._scan_venvs).pack(side=tk.LEFT)
        self.venv_status = tk.StringVar(value="Cliquez sur Scanner pour détecter les venvs.")
        tk.Label(venv_bar, textvariable=self.venv_status, fg="#555").pack(side=tk.LEFT, padx=10)

        # PanedWindow vertical dans l'onglet : liste venvs (haut) / comparaison (bas)
        venv_vpaned = tk.PanedWindow(tab_venv, orient=tk.VERTICAL, sashwidth=5)
        venv_vpaned.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))

        list_frame = tk.LabelFrame(venv_vpaned, text="Venvs détectés", padx=4, pady=4)
        venv_vpaned.add(list_frame, stretch="never", minsize=100)

        cols = ("Nom", "Chemin", "Python", "Héritage global")
        self.venv_tree = ttk.Treeview(list_frame, columns=cols,
                                      show="headings", height=4, selectmode="browse")
        for col, w in zip(cols, [180, 430, 80, 130]):
            self.venv_tree.heading(col, text=col)
            self.venv_tree.column(col, width=w)
        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.venv_tree.yview)
        self.venv_tree.configure(yscrollcommand=vsb.set)
        self.venv_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.venv_tree.bind("<<TreeviewSelect>>", self._on_venv_select)

        cmp_frame = tk.Frame(venv_vpaned)
        venv_vpaned.add(cmp_frame, stretch="always", minsize=200)
        cmp_frame.rowconfigure(0, weight=1)
        cmp_frame.columnconfigure(0, weight=1)
        self.venv_panel = ComparePanel(cmp_frame, self._log)
        self.venv_panel.grid(row=0, column=0, sticky="nsew")

        # Onglet 3 — Créer un venv
        tab_create = tk.Frame(self.notebook)
        self.notebook.add(tab_create, text="  Créer un environnement virtuel  ")
        self._build_create_tab(tab_create)

        self.log_text = tk.Text(self._log_frame, state=tk.DISABLED,
                                bg="#1e1e1e", fg="#d4d4d4", font=("Courier", 9), height=8)
        log_sb = ttk.Scrollbar(self._log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_sb.grid(row=0, column=1, sticky="ns")

    def _build_create_tab(self, parent):
        parent.columnconfigure(1, weight=1)

        # Titre explicatif
        tk.Label(parent, text="Créer un nouvel environnement virtuel Python",
                 font=("Helvetica", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 4))
        tk.Label(parent,
                 text="L'environnement sera créé à partir d'une version Python déjà installée sur ce poste.",
                 fg="#555").grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 12))

        # Version Python
        tk.Label(parent, text="Version Python :").grid(row=2, column=0, sticky="w", padx=12, pady=4)
        self.py_version_var = tk.StringVar()
        self.py_combo = ttk.Combobox(parent, textvariable=self.py_version_var,
                                     state="readonly", width=20)
        self.py_combo.grid(row=2, column=1, sticky="w", padx=4)
        tk.Button(parent, text="Détecter", command=self._detect_pythons).grid(
            row=2, column=2, padx=8)

        # Nom du venv
        tk.Label(parent, text="Nom du venv :").grid(row=3, column=0, sticky="w", padx=12, pady=4)
        self.venv_name_var = tk.StringVar(value="mon-venv")
        tk.Entry(parent, textvariable=self.venv_name_var, width=30).grid(
            row=3, column=1, sticky="w", padx=4)

        # Dossier parent
        tk.Label(parent, text="Dossier de destination :").grid(
            row=4, column=0, sticky="w", padx=12, pady=4)
        self.venv_dir_var = tk.StringVar(value=os.path.expanduser("~"))
        tk.Entry(parent, textvariable=self.venv_dir_var, width=50).grid(
            row=4, column=1, sticky="ew", padx=4)
        tk.Button(parent, text="Parcourir…", command=self._browse_venv_dir).grid(
            row=4, column=2, padx=8)

        # Option héritage global
        self.system_site_var = tk.BooleanVar(value=False)
        tk.Checkbutton(parent, text="Hériter des packages globaux (--system-site-packages)",
                       variable=self.system_site_var).grid(
            row=5, column=0, columnspan=3, sticky="w", padx=12, pady=4)

        # Chemin résultant (aperçu)
        tk.Label(parent, text="Chemin final :").grid(row=6, column=0, sticky="w", padx=12, pady=4)
        self.venv_preview_var = tk.StringVar()
        tk.Label(parent, textvariable=self.venv_preview_var, fg="#2980b9").grid(
            row=6, column=1, columnspan=2, sticky="w", padx=4)

        def _update_preview(*_):
            d = self.venv_dir_var.get().strip()
            n = self.venv_name_var.get().strip()
            self.venv_preview_var.set(os.path.join(d, n) if d and n else "")
        self.venv_dir_var.trace_add("write", _update_preview)
        self.venv_name_var.trace_add("write", _update_preview)
        _update_preview()

        # Bouton créer
        tk.Button(parent, text="Créer le venv",
                  bg="#27ae60", fg="white", font=("Helvetica", 11, "bold"),
                  command=self._create_venv).grid(
            row=7, column=0, columnspan=3, pady=16)

        # Lancer la détection automatiquement
        self.after(500, self._detect_pythons)

    # ── Python système ────────────────────────────────────────────────────────

    def _load_system(self):
        tag = self.sys_py_var.get().strip()
        if not tag:
            return
        self._log(f"Chargement des packages Python système ({tag})…")
        threading.Thread(target=self._do_load_system, args=(tag,), daemon=True).start()

    def _do_load_system(self, tag):
        # Récupère version complète et packages via py
        try:
            ver_out = subprocess.check_output(
                ["py", tag, "-c",
                 "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            ver_out = tag.lstrip("-V:").strip()

        installed = _get_system_packages_for(tag)
        pip_exe_cmd = ["py", tag, "-m", "pip"]
        whls = get_local_whls(target_python_version=ver_out)
        self.after(0, lambda: self._apply_system(installed, whls, pip_exe_cmd, tag, ver_out))

    def _apply_system(self, installed, whls, pip_exe_cmd, tag, ver_out):
        self.sys_panel.load(installed, whls, pip_exe_cmd)
        self.sys_info.set(f"Python {ver_out}  —  {len(installed)} packages")
        self._log(f"Système {tag} : {len(installed)} packages, {len(whls)} .whl disponibles.")

    # ── Venvs ─────────────────────────────────────────────────────────────────

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
                                  values=(v["name"], v["path"], v["python_version"], heritage))
        self.venv_status.set(f"{len(venvs)} venv(s) détecté(s) dans C:\\Users\\")
        self._log(f"Scan terminé : {len(venvs)} venv(s) trouvé(s).")

    def _on_venv_select(self, _event):
        sel = self.venv_tree.selection()
        if not sel:
            return
        venv = next((v for v in self._venvs if v["path"] == sel[0]), None)
        if not venv:
            return
        self._log(f"Chargement : {venv['name']} (Python {venv['python_version']})…")
        threading.Thread(target=self._load_venv_packages, args=(venv,), daemon=True).start()

    def _load_venv_packages(self, venv):
        if not venv["pip_exe"]:
            self.after(0, lambda: self._log("pip introuvable dans ce venv."))
            return
        installed = get_venv_packages(venv["pip_exe"])
        whls = get_local_whls(target_python_version=venv["python_version"])
        self.after(0, lambda: self.venv_panel.load(installed, whls, venv["pip_exe"]))
        self.after(0, lambda: self._log(
            f"{venv['name']} : {len(installed)} packages — "
            f"{'hérite du global' if venv['system_site'] else 'isolé'}"
        ))

    # ── Créer un venv ─────────────────────────────────────────────────────────

    def _detect_pythons(self):
        self._log("Détection des versions Python installées…")
        threading.Thread(target=self._do_detect_pythons, daemon=True).start()

    def _do_detect_pythons(self):
        versions = detect_python_installs()
        self.after(0, lambda: self._set_python_versions(versions))

    def _set_python_versions(self, versions):
        # Combo onglet "Créer un venv"
        self.py_combo["values"] = versions
        if versions:
            self.py_combo.current(0)
        # Combo onglet "Python système"
        self.sys_py_combo["values"] = versions
        if versions:
            self.sys_py_combo.current(0)
            self._log(f"Versions Python détectées : {', '.join(versions)}")
            self._load_system()
        else:
            self._log("Aucune version Python détectée via le Python Launcher.")

    def _browse_venv_dir(self):
        d = filedialog.askdirectory(title="Choisir le dossier de destination")
        if d:
            self.venv_dir_var.set(d)

    def _create_venv(self):
        py_ver = self.py_version_var.get().strip()
        name = self.venv_name_var.get().strip()
        dest = self.venv_dir_var.get().strip()

        if not py_ver:
            messagebox.showwarning("Version manquante",
                                   "Sélectionnez une version Python (cliquez sur Détecter).")
            return
        if not name:
            messagebox.showwarning("Nom manquant", "Saisissez un nom pour le venv.")
            return
        if not dest:
            messagebox.showwarning("Dossier manquant", "Choisissez un dossier de destination.")
            return

        venv_path = os.path.join(dest, name)
        if os.path.exists(venv_path):
            messagebox.showerror("Existe déjà",
                                 f"Un dossier existe déjà à :\n{venv_path}")
            return

        cmd = ["py", py_ver, "-m", "venv", venv_path]
        if self.system_site_var.get():
            cmd.append("--system-site-packages")

        self._log(f"Création du venv : {venv_path}")
        self._log(f"Commande : {' '.join(cmd)}")
        threading.Thread(target=self._do_create_venv,
                         args=(cmd, venv_path), daemon=True).start()

    def _do_create_venv(self, cmd, venv_path):
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout:
                self._log(line.rstrip())
            proc.wait()
            if proc.returncode == 0:
                self._log(f"✓ Venv créé : {venv_path}")
                self.after(0, lambda: messagebox.showinfo(
                    "Venv créé", f"Environnement virtuel créé avec succès :\n{venv_path}"))
            else:
                self._log(f"✗ Erreur lors de la création (code {proc.returncode}).")
        except FileNotFoundError:
            self._log("✗ Python Launcher (py) introuvable. Vérifiez que Python est installé.")
        except Exception as e:
            self._log(f"✗ Exception : {e}")

    # ── Journal ───────────────────────────────────────────────────────────────

    def _toggle_journal(self):
        if self._log_visible:
            self._log_frame.pack_forget()
            self._log_visible = False
            self._log_btn.configure(bg="#34495e")
        else:
            self._log_frame.pack(fill=tk.BOTH, padx=8, pady=(0, 6))
            self._log_visible = True
            self._log_btn.configure(bg="#e67e22")

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
