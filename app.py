import sqlite3
import os
import sys
import atexit
import getpass
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'projets.db')
LOCK_FILE = os.path.join(BASE_DIR, '.lock')


# === SYSTEME DE VERROU ===

def check_lock():
    """Vérifie si l'application est déjà utilisée par quelqu'un d'autre."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                content = f.read().strip()
                user, timestamp = content.split('|')
                print(f"\n{'='*60}")
                print(f"  ⚠️  ATTENTION : L'application est déjà utilisée")
                print(f"  Par : {user}")
                print(f"  Depuis : {timestamp}")
                print(f"{'='*60}\n")
                response = input("Voulez-vous forcer le lancement ? (o/n) : ")
                if response.lower() != 'o':
                    print("Abandon.")
                    sys.exit(0)
        except (ValueError, IOError):
            pass


def create_lock():
    """Crée le fichier verrou."""
    user = getpass.getuser()
    timestamp = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    with open(LOCK_FILE, 'w') as f:
        f.write(f"{user}|{timestamp}")


def remove_lock():
    """Supprime le fichier verrou."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except IOError:
        pass


# === BASE DE DONNÉES ===

def get_db():
    """Connexion à la base de données SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate_db():
    """Migration automatique : ajoute les colonnes manquantes."""
    conn = get_db()
    cursor = conn.cursor()

    # Vérifier les colonnes existantes de la table projects
    cursor.execute("PRAGMA table_info(projects)")
    existing_columns = [row['name'] for row in cursor.fetchall()]

    # Ajouter project_type si manquante
    if 'project_type' not in existing_columns:
        cursor.execute("ALTER TABLE projects ADD COLUMN project_type TEXT DEFAULT ''")
        print("[MIGRATION] Ajout de la colonne 'project_type' à la table projects")

    # Ajouter ticket_number si manquante
    if 'ticket_number' not in existing_columns:
        cursor.execute("ALTER TABLE projects ADD COLUMN ticket_number TEXT DEFAULT ''")
        print("[MIGRATION] Ajout de la colonne 'ticket_number' à la table projects")

    # Ajouter ticket_date si manquante
    if 'ticket_date' not in existing_columns:
        cursor.execute("ALTER TABLE projects ADD COLUMN ticket_date TEXT DEFAULT ''")
        print("[MIGRATION] Ajout de la colonne 'ticket_date' à la table projects")

    conn.commit()
    conn.close()


def init_db():
    """Initialise la base de données avec les tables nécessaires."""
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            category_id INTEGER,
            project_type TEXT DEFAULT '',
            ticket_number TEXT DEFAULT '',
            ticket_date TEXT DEFAULT '',
            cadrage_status TEXT DEFAULT 'Non démarré',
            cadrage_start TEXT DEFAULT '',
            cadrage_end TEXT DEFAULT '',
            recette_status TEXT DEFAULT 'Non démarré',
            recette_start TEXT DEFAULT '',
            recette_end TEXT DEFAULT '',
            preprod_status TEXT DEFAULT 'Non démarré',
            preprod_start TEXT DEFAULT '',
            preprod_end TEXT DEFAULT '',
            mep_status TEXT DEFAULT 'Non démarré',
            mep_start TEXT DEFAULT '',
            mep_end TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );

        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS dqas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'En cours',
            date TEXT DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS formations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            type TEXT NOT NULL DEFAULT 'Outil',
            date TEXT DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
    ''')
    conn.commit()
    conn.close()
    migrate_db()


# === ROUTES PAGES ===

@app.route('/')
def index():
    return render_template('index.html')


# === API CATEGORIES (FLGE) ===

@app.route('/api/categories', methods=['GET'])
def get_categories():
    conn = get_db()
    categories = conn.execute('SELECT * FROM categories ORDER BY name').fetchall()
    conn.close()
    return jsonify([dict(c) for c in categories])


@app.route('/api/categories', methods=['POST'])
def create_category():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO categories (name) VALUES (?)', (data['name'],))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Cette FLGE existe déjà'}), 400
    conn.close()
    return jsonify({'success': True}), 201


@app.route('/api/categories/<int:cat_id>', methods=['DELETE'])
def delete_category(cat_id):
    conn = get_db()
    conn.execute('DELETE FROM categories WHERE id = ?', (cat_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# === API PROJETS ===

@app.route('/api/projects', methods=['GET'])
def get_projects():
    conn = get_db()
    projects = conn.execute('''
        SELECT p.*, c.name as category_name
        FROM projects p
        LEFT JOIN categories c ON p.category_id = c.id
        ORDER BY p.created_at DESC
    ''').fetchall()

    result = []
    for p in projects:
        project = dict(p)
        # Récupérer les intervenants
        members = conn.execute(
            'SELECT * FROM members WHERE project_id = ?', (p['id'],)
        ).fetchall()
        project['members'] = [dict(m) for m in members]
        # Récupérer les DQA
        dqas = conn.execute(
            'SELECT * FROM dqas WHERE project_id = ?', (p['id'],)
        ).fetchall()
        project['dqas'] = [dict(d) for d in dqas]
        # Récupérer les formations
        formations = conn.execute(
            'SELECT * FROM formations WHERE project_id = ?', (p['id'],)
        ).fetchall()
        project['formations'] = [dict(f) for f in formations]
        result.append(project)

    conn.close()
    return jsonify(result)


@app.route('/api/projects', methods=['POST'])
def create_project():
    data = request.json
    conn = get_db()
    cursor = conn.execute('''
        INSERT INTO projects (name, description, category_id, project_type, ticket_number, ticket_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        data['name'],
        data.get('description', ''),
        data.get('category_id') or None,
        data.get('project_type', ''),
        data.get('ticket_number', ''),
        data.get('ticket_date', '')
    ))
    conn.commit()
    project_id = cursor.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': project_id}), 201


@app.route('/api/projects/<int:project_id>', methods=['PUT'])
def update_project(project_id):
    data = request.json
    conn = get_db()
    conn.execute('''
        UPDATE projects SET
            name=?, description=?, category_id=?, project_type=?,
            ticket_number=?, ticket_date=?,
            cadrage_status=?, cadrage_start=?, cadrage_end=?,
            recette_status=?, recette_start=?, recette_end=?,
            preprod_status=?, preprod_start=?, preprod_end=?,
            mep_status=?, mep_start=?, mep_end=?
        WHERE id=?
    ''', (
        data['name'], data.get('description', ''),
        data.get('category_id') or None, data.get('project_type', ''),
        data.get('ticket_number', ''), data.get('ticket_date', ''),
        data.get('cadrage_status', 'Non démarré'),
        data.get('cadrage_start', ''), data.get('cadrage_end', ''),
        data.get('recette_status', 'Non démarré'),
        data.get('recette_start', ''), data.get('recette_end', ''),
        data.get('preprod_status', 'Non démarré'),
        data.get('preprod_start', ''), data.get('preprod_end', ''),
        data.get('mep_status', 'Non démarré'),
        data.get('mep_start', ''), data.get('mep_end', ''),
        project_id
    ))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    conn = get_db()
    conn.execute('DELETE FROM members WHERE project_id = ?', (project_id,))
    conn.execute('DELETE FROM dqas WHERE project_id = ?', (project_id,))
    conn.execute('DELETE FROM formations WHERE project_id = ?', (project_id,))
    conn.execute('DELETE FROM projects WHERE id = ?', (project_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# === API INTERVENANTS ===

@app.route('/api/projects/<int:project_id>/members', methods=['POST'])
def add_member(project_id):
    data = request.json
    conn = get_db()
    conn.execute(
        'INSERT INTO members (project_id, name, role) VALUES (?, ?, ?)',
        (project_id, data['name'], data.get('role', ''))
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True}), 201


@app.route('/api/members/<int:member_id>', methods=['DELETE'])
def delete_member(member_id):
    conn = get_db()
    conn.execute('DELETE FROM members WHERE id = ?', (member_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# === API DQA ===

@app.route('/api/projects/<int:project_id>/dqas', methods=['POST'])
def add_dqa(project_id):
    data = request.json
    conn = get_db()
    conn.execute(
        'INSERT INTO dqas (project_id, status, date) VALUES (?, ?, ?)',
        (project_id, data.get('status', 'En cours'), data.get('date', ''))
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True}), 201


@app.route('/api/dqas/<int:dqa_id>', methods=['DELETE'])
def delete_dqa(dqa_id):
    conn = get_db()
    conn.execute('DELETE FROM dqas WHERE id = ?', (dqa_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# === API FORMATIONS ===

@app.route('/api/projects/<int:project_id>/formations', methods=['POST'])
def add_formation(project_id):
    data = request.json
    conn = get_db()
    conn.execute(
        'INSERT INTO formations (project_id, type, date) VALUES (?, ?, ?)',
        (project_id, data.get('type', 'Outil'), data.get('date', ''))
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True}), 201


@app.route('/api/formations/<int:formation_id>', methods=['DELETE'])
def delete_formation(formation_id):
    conn = get_db()
    conn.execute('DELETE FROM formations WHERE id = ?', (formation_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# === API VUE SEMAINE ===

@app.route('/api/projects/week', methods=['GET'])
def get_week_projects():
    """Retourne les projets ayant une date dans la semaine spécifiée."""
    offset = int(request.args.get('offset', 0))

    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    friday = monday + timedelta(days=4)

    monday_str = monday.isoformat()
    friday_str = friday.isoformat()

    conn = get_db()
    projects = conn.execute('''
        SELECT p.*, c.name as category_name
        FROM projects p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE
            (p.cadrage_start BETWEEN ? AND ?) OR (p.cadrage_end BETWEEN ? AND ?) OR
            (p.recette_start BETWEEN ? AND ?) OR (p.recette_end BETWEEN ? AND ?) OR
            (p.preprod_start BETWEEN ? AND ?) OR (p.preprod_end BETWEEN ? AND ?) OR
            (p.mep_start BETWEEN ? AND ?) OR (p.mep_end BETWEEN ? AND ?) OR
            (p.ticket_date BETWEEN ? AND ?)
        ORDER BY p.name
    ''', (
        monday_str, friday_str, monday_str, friday_str,
        monday_str, friday_str, monday_str, friday_str,
        monday_str, friday_str, monday_str, friday_str,
        monday_str, friday_str, monday_str, friday_str,
        monday_str, friday_str
    )).fetchall()

    result = []
    for p in projects:
        project = dict(p)
        members = conn.execute(
            'SELECT * FROM members WHERE project_id = ?', (p['id'],)
        ).fetchall()
        project['members'] = [dict(m) for m in members]
        dqas = conn.execute(
            'SELECT * FROM dqas WHERE project_id = ? AND date BETWEEN ? AND ?',
            (p['id'], monday_str, friday_str)
        ).fetchall()
        project['dqas'] = [dict(d) for d in dqas]
        formations = conn.execute(
            'SELECT * FROM formations WHERE project_id = ? AND date BETWEEN ? AND ?',
            (p['id'], monday_str, friday_str)
        ).fetchall()
        project['formations'] = [dict(f) for f in formations]
        result.append(project)

    conn.close()
    return jsonify({
        'projects': result,
        'monday': monday_str,
        'friday': friday_str
    })


# === LANCEMENT ===

if __name__ == '__main__':
    check_lock()
    create_lock()
    atexit.register(remove_lock)
    init_db()
    print(f"\n✅ Application lancée sur http://localhost:5000\n")
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
