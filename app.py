"""
app.py — Application Flask — Estimation Élec
Sprint 4 : Full Stack Local

Lancement :
    python app.py
    → http://localhost:5000
"""

import os
import sys
import json
import time
import threading
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

# ─── Redirection stdout/stderr pour mode pythonw.exe ─────────────────────────
# Quand l'app est lancée via pythonw.exe (mode invisible depuis le .vbs),
# sys.stdout et sys.stderr sont None. Le moindre print() fait crasher Flask.
# On redirige donc tout vers logs/serveur.log dès le début.
if sys.stdout is None or sys.stderr is None:
    _log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'logs', 'serveur.log'
    )
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)
    _log_file = open(_log_path, 'a', buffering=1, encoding='utf-8')
    sys.stdout = _log_file
    sys.stderr = _log_file

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, send_file, flash
)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

import models
from scripts.engine_ratios import get_dpgf_tree_with_ratios, compute_ratios
from scripts.export_excel import export_dpgf_excel
from loguru import logger

# ─── Logging erreurs applicatives ────────────────────────────────────────────
_logs_dir = os.path.join(PROJECT_DIR, 'logs')
os.makedirs(_logs_dir, exist_ok=True)
logger.add(
    os.path.join(_logs_dir, 'app_errors.log'),
    level='ERROR',
    rotation='10 MB',
    retention='30 days',
    format='{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}',
    encoding='utf-8',
)

# ─── Configuration ────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = 'elec-estim-2026-local'
app.config['UPLOAD_FOLDER']   = os.path.join(PROJECT_DIR, 'uploads')
app.config['EXPORT_FOLDER']   = os.path.join(PROJECT_DIR, 'exports')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB

for folder in [app.config['UPLOAD_FOLDER'], app.config['EXPORT_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# Initialise les tables web au démarrage
models.ensure_app_tables()


# ─── Gestionnaires d'erreurs HTTP globaux ────────────────────────────────────
# Intercepte les erreurs que Flask génère AVANT d'atteindre la route
# (ex : 413 payload trop grand, 400 JSON malformé) et les logue dans app_errors.log

@app.errorhandler(413)
def request_too_large(e):
    logger.error(f"413 Request Entity Too Large | {request.path} | {e}")
    return jsonify({'status': 'error', 'code': 413, 'message': 'Payload trop volumineux (limite 20 MB)'}), 413

@app.errorhandler(400)
def bad_request(e):
    logger.error(f"400 Bad Request | {request.path} | {e}")
    return jsonify({'status': 'error', 'code': 400, 'message': f'Requête invalide : {e.description}'}), 400

@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    """Capture toutes les exceptions non gérées, les log, et retourne du JSON."""
    logger.error(f"Exception non gérée | {request.method} {request.path} | {type(e).__name__}: {e}")
    logger.exception(e)
    return jsonify({'status': 'error', 'code': 500, 'message': str(e)}), 500


# ─── Filtre Jinja ─────────────────────────────────────────────────────────────

@app.template_filter('eur')
def format_eur(value):
    if value is None or value == 0:
        return '—'
    return f"{float(value):,.0f} €".replace(',', '\u202f')


@app.template_filter('pct')
def format_pct(value):
    if value is None:
        return '—'
    v = float(value)
    sign = '+' if v >= 0 else ''
    return f"{sign}{v:.1f}%"


@app.template_filter('date_fr')
def date_fr_filter(value):
    """Convertit YYYY-MM-DD → JJ-MM-AAAA."""
    if not value:
        return ''
    s = str(value)[:10]
    parts = s.split('-')
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return s


# ─── Pages principales ────────────────────────────────────────────────────────

@app.route('/')
def index():
    affaires   = models.get_affaires()
    categories = models.get_categories()
    return render_template('index.html',
                           affaires=affaires,
                           categories=categories)


def _compute_preview_context():
    """Ratios €/m² par lot (complexité 1.0) + date de dernière MAJ de la BDD.

    Utilisé par la page Création / Édition d'affaire pour l'estimation
    prévisionnelle CFO/CFA/PV.
    """
    try:
        ratios = compute_ratios(target_sdo=1000.0,
                                target_complexity_cfo=1.0,
                                target_complexity_cfa=1.0,
                                target_complexity_pv=1.0)
        totals = {'CFO': 0.0, 'CFA': 0.0, 'PV': 0.0}
        for r in ratios.values():
            lot = r.get('lot') or 'CFO'
            totals[lot] = totals.get(lot, 0.0) + (r.get('total_estime') or 0.0)
        ratio_m2_cfo = totals['CFO'] / 1000.0
        ratio_m2_cfa = totals['CFA'] / 1000.0
        ratio_m2_pv  = totals['PV']  / 1000.0
    except Exception:
        ratio_m2_cfo, ratio_m2_cfa, ratio_m2_pv = 113.13, 0.0, 0.0

    try:
        mtime = os.path.getmtime(models.DB_PATH)
        maj_date = datetime.fromtimestamp(mtime).strftime('%d-%m-%Y')
    except Exception:
        maj_date = None

    return {
        'ratio_m2_cfo': round(ratio_m2_cfo, 2),
        'ratio_m2_cfa': round(ratio_m2_cfa, 2),
        'ratio_m2_pv':  round(ratio_m2_pv,  2),
        'maj_date_bdd': maj_date,
    }


@app.route('/affaire/new', methods=['GET', 'POST'])
def affaire_new():
    categories = models.get_categories()

    if request.method == 'POST':
        data = {
            'name':                request.form.get('name', 'Nouvelle Affaire'),
            'client':              request.form.get('client'),
            'adresse':             request.form.get('adresse'),
            'surface_sdo':         float(request.form.get('surface_sdo') or 1000),
            'category_id':         request.form.get('category_id') or None,
            'coef_complexity_cfo': float(request.form.get('coef_complexity_cfo') or 1.0),
            'coef_complexity_cfa': float(request.form.get('coef_complexity_cfa') or 1.0),
            'coef_complexity_pv':  float(request.form.get('coef_complexity_pv')  or 1.0),
            'coef_risque':         float(request.form.get('coef_risque') or 1.0),
            'kva_cible':           float(request.form.get('kva_cible') or 800.0),
            'phase_etude':         request.form.get('phase_etude') or 'APD',
            'taux_phase':          float(request.form.get('taux_phase') or 3.0),
            'taux_incertitude':    float(request.form.get('taux_incertitude') or 3.0),
            'notes':               request.form.get('notes'),
        }
        affaire_id = models.create_affaire(data)
        # Pas d'injection ici : la page Estimation affiche le catalogue avec qté 0
        # tant qu'aucune ligne n'existe ; le calculateur injecte au premier passage si besoin.
        return redirect(url_for('affaire_estimation', affaire_id=affaire_id))

    return render_template('affaire_new.html',
                           categories=categories,
                           **_compute_preview_context())


@app.route('/affaire/<int:affaire_id>/edit', methods=['GET', 'POST'])
def affaire_edit(affaire_id):
    affaire    = models.get_affaire(affaire_id)
    categories = models.get_categories()
    if not affaire:
        flash('Affaire introuvable', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        data = {
            'name':                request.form.get('name', affaire['name']),
            'client':              request.form.get('client'),
            'adresse':             request.form.get('adresse'),
            'surface_sdo':         float(request.form.get('surface_sdo') or affaire['surface_sdo']),
            'category_id':         request.form.get('category_id') or None,
            'coef_complexity_cfo': float(request.form.get('coef_complexity_cfo') or 1.0),
            'coef_complexity_cfa': float(request.form.get('coef_complexity_cfa') or 1.0),
            'coef_complexity_pv':  float(request.form.get('coef_complexity_pv')  or 1.0),
            'coef_risque':         float(request.form.get('coef_risque') or 0.0),
            'kva_cible':           float(request.form.get('kva_cible') or 800.0),
            'phase_etude':         request.form.get('phase_etude') or 'APD',
            'taux_phase':          float(request.form.get('taux_phase') or 3.0),
            'taux_incertitude':    float(request.form.get('taux_incertitude') or 3.0),
            'notes':               request.form.get('notes'),
            'statut':              affaire.get('statut', 'brouillon'),
        }
        models.update_affaire(affaire_id, data)
        return redirect(url_for('affaire_estimation', affaire_id=affaire_id))

    return render_template('affaire_new.html',
                           categories=categories,
                           affaire=affaire,
                           edit_mode=True,
                           **_compute_preview_context())


@app.route('/affaire/<int:affaire_id>')
def affaire_view(affaire_id):
    affaire = models.get_affaire(affaire_id)
    if not affaire:
        flash('Affaire introuvable', 'error')
        return redirect(url_for('index'))

    # Calcul des ratios pour cette affaire
    tree = get_dpgf_tree_with_ratios(
        target_sdo           = affaire['surface_sdo'],
        target_complexity_cfo= affaire['coef_complexity_cfo'],
        target_complexity_cfa= affaire['coef_complexity_cfa'],
        target_complexity_pv = affaire['coef_complexity_pv'],
    )

    # Sprint 7 bis : calcul du ratio €/m² de référence (théorique, basé sur total_estime)
    # AVANT d'écraser art['total'] avec qty*unit_price. Ces valeurs servent de fallback
    # d'affichage quand les articles sont à qty=0 à l'init.
    sdo_val = float(affaire['surface_sdo']) or 1.0
    for chapter in tree:
        chap_ref_total = 0.0
        for section in chapter['sections']:
            sect_ref_total = sum((art.get('total') or 0) for art in section['articles'])
            section['ratio_ref_m2'] = sect_ref_total / sdo_val if sdo_val else 0
            chap_ref_total += sect_ref_total
        chapter['ratio_ref_m2'] = chap_ref_total / sdo_val if sdo_val else 0

    # Fallback : aucune ligne sauvegardée → on injecte les 285 maintenant.
    # Sinon : CHARGEMENT STRICT des valeurs existantes (Sprint 7).
    saved_lines = models.get_affaire_lines(affaire_id)
    if not saved_lines:
        _initialize_affaire_lines(affaire_id, affaire)
        saved_lines = models.get_affaire_lines(affaire_id)

    # Application STRICTE des lignes sauvegardées sur le tree (pas de ré-injection
    # depuis les ratios si la ligne existe déjà en DB — Sprint 7 §Chargement strict)
    for chapter in tree:
        for section in chapter['sections']:
            for art in section['articles']:
                saved = saved_lines.get(art['id'])
                if saved:
                    art['qty']               = saved['quantity']      if saved['quantity']      is not None else 0
                    art['unit_price']        = saved['unit_price_ht'] if saved['unit_price_ht'] is not None else art['unit_price']
                    art['total']             = (art['qty'] or 0) * (art['unit_price'] or 0)
                    art['is_included']       = bool(saved['is_included'])
                    art['quantity_source']   = saved['quantity_source']
                    art['unit_price_source'] = saved['unit_price_source']
                    # Sprint 7 : override d'unité par affaire
                    if saved.get('unit_override'):
                        art['unit']          = saved['unit_override']
                    art['unit_source']       = saved.get('unit_source') or 'ratio'
                else:
                    # Ligne orpheline (article ajouté au référentiel après création de l'affaire)
                    art['qty']         = 0
                    art['is_included'] = True

    # Sprint 7 : charge les paramètres chapitre/section (checkbox + mode Macro)
    chapter_settings = models.get_chapter_settings(affaire_id)
    for chapter in tree:
        chap_key = f"chap:{chapter['designation']}"
        cs = chapter_settings.get(chap_key, {})
        chapter['is_included'] = bool(cs.get('is_included', 1))
        chapter['use_macro']   = bool(cs.get('use_macro',   1))
        chapter['qty']         = cs.get('qty', 1.0)
        for section in chapter['sections']:
            sect_key = f"sect:{chapter['designation']}|{section['designation']}"
            ss = chapter_settings.get(sect_key, {})
            section['is_included'] = bool(ss.get('is_included', 1))
            section['use_macro']   = bool(ss.get('use_macro',   0))
            section['qty']         = ss.get('qty', 1.0)

    # Totaux par lot
    totals = {'CFO': 0.0, 'CFA': 0.0, 'PV': 0.0}
    for chapter in tree:
        for section in chapter['sections']:
            for art in section['articles']:
                if art['is_included']:
                    lot = art['lot'] or 'CFO'
                    totals[lot] = totals.get(lot, 0) + (art['total'] or 0)

    totals['ALL'] = sum(totals.values())
    totals['m2']  = totals['ALL'] / affaire['surface_sdo'] if affaire['surface_sdo'] else 0

    categories = models.get_categories()

    return render_template('affaire.html',
                           affaire=affaire,
                           tree=tree,
                           totals=totals,
                           categories=categories)


@app.route('/affaire/<int:affaire_id>/estimation')
def affaire_estimation(affaire_id):
    """Page saisie « double calque » : référentiel (lecture seule) + estimation éditable."""
    affaire = models.get_affaire(affaire_id)
    if not affaire:
        flash('Affaire introuvable', 'error')
        return redirect(url_for('index'))

    catalog = models.get_estimation_catalog_rows(affaire_id)
    customs = models.get_estimation_custom_rows(affaire_id)
    totals = models.compute_estimation_kpis(affaire_id)
    chapter_state = models.get_estimation_chapter_state(affaire_id)
    section_state = models.get_estimation_section_state(affaire_id)
    affaires = models.get_affaires()

    return render_template(
        'affaire_estimation.html',
        affaire=affaire,
        affaires=affaires,
        affaire_id=affaire_id,
        catalog_rows=catalog,
        custom_rows=customs,
        totals=totals,
        chapter_state=chapter_state,
        section_state=section_state,
    )


@app.route('/api/affaire/<int:affaire_id>/estimation/save', methods=['POST'])
def affaire_estimation_save(affaire_id):
    """Auto-save des lignes depuis la page Estimation d'affaire (debounce JS)."""
    if not models.get_affaire(affaire_id):
        return jsonify({'status': 'error', 'code': 404, 'message': 'Affaire introuvable'}), 404
    data = request.get_json(force=True) or {}
    raw_ch = data.get('changes')
    if raw_ch is None or not isinstance(raw_ch, list):
        changes = []
    else:
        changes = raw_ch
    try:
        out = models.save_estimation_changes(affaire_id, changes)
        return jsonify(out)
    except Exception as exc:
        logger.error(f"estimation/save FAILED | affaire_id={affaire_id} | {exc}")
        logger.exception(exc)
        return jsonify({'status': 'error', 'code': 500, 'message': str(exc)}), 500


@app.route('/api/affaire/<int:affaire_id>/save', methods=['POST'])
def affaire_save(affaire_id):
    data = request.get_json(force=True)
    lines = data.get('lines', [])
    models.save_affaire_lines(affaire_id, lines)

    # Total effectif (ratio fallback inclus) — envoyé par le JS pour le dashboard
    if 'total_estime' in data:
        models.save_total_estime(affaire_id, float(data['total_estime']))

    # Met à jour les métadonnées de l'affaire si envoyées
    if 'affaire' in data:
        models.update_affaire(affaire_id, data['affaire'])

    return jsonify({'status': 'ok', 'saved': len(lines)})


@app.route('/api/affaire/<int:affaire_id>/params', methods=['POST'])
def affaire_params(affaire_id):
    """Auto-save des paramètres de cadrage (SDO, kVA, phase, taux)."""
    data = request.get_json(force=True)
    models.update_affaire_params(affaire_id, data)
    synced = 0
    if 'surface_sdo' in data and models.get_affaire(affaire_id):
        try:
            synced = models.batch_sync_estimation_m2_quantities(
                affaire_id, float(data['surface_sdo'])
            )
        except (TypeError, ValueError):
            synced = 0
    kpis = models.compute_estimation_kpis(affaire_id)
    models.save_total_estime(affaire_id, kpis['ALL'])
    return jsonify({'status': 'ok', 'totals': kpis, 'm2_rows_updated': synced})


@app.route('/api/affaire/<int:affaire_id>/chapter_settings', methods=['POST'])
def affaire_chapter_settings(affaire_id):
    """Sprint 7 : auto-save des checkboxes / mode Macro des chapitres et sections."""
    data     = request.get_json(force=True)
    settings = data.get('settings', [])
    models.save_chapter_settings(affaire_id, settings)
    kpis = models.compute_estimation_kpis(affaire_id)
    models.save_total_estime(affaire_id, kpis['ALL'])
    return jsonify({'status': 'ok', 'saved': len(settings), 'totals': kpis})


@app.route('/api/affaire/<int:affaire_id>/delete', methods=['POST'])
def affaire_delete(affaire_id):
    models.delete_affaire(affaire_id)
    return jsonify({'status': 'ok'})


@app.route('/api/affaire/<int:affaire_id>/export')
def affaire_export(affaire_id):
    affaire = models.get_affaire(affaire_id)
    if not affaire:
        return jsonify({'error': 'Affaire introuvable'}), 404

    tree = get_dpgf_tree_with_ratios(
        target_sdo            = affaire['surface_sdo'],
        target_complexity_cfo = affaire['coef_complexity_cfo'],
        target_complexity_cfa = affaire['coef_complexity_cfa'],
        target_complexity_pv  = affaire['coef_complexity_pv'],
    )

    saved_lines = models.get_affaire_lines(affaire_id)
    if saved_lines:
        for chapter in tree:
            for section in chapter['sections']:
                for art in section['articles']:
                    saved = saved_lines.get(art['id'])
                    if saved:
                        art['qty']         = saved['quantity'] or art['qty']
                        art['unit_price']  = saved['unit_price_ht'] or art['unit_price']
                        art['total']       = (art['qty'] or 0) * (art['unit_price'] or 0)
                        art['is_included'] = bool(saved['is_included'])

    filename = f"DPGF_{affaire['name'].replace(' ','_')}_{date.today().isoformat()}.xlsx"
    filepath = os.path.join(app.config['EXPORT_FOLDER'], filename)

    export_dpgf_excel(affaire, tree, filepath)

    return send_file(filepath, as_attachment=True, download_name=filename)


# ─── Import ───────────────────────────────────────────────────────────────────

@app.route('/bibliotheque')
@app.route('/bibliotheque/<int:affaire_id>')
def bibliotheque(affaire_id=None):
    data = models.get_bibliotheque_data(affaire_id)
    affaire_courante = None
    if affaire_id:
        for a in data['affaires']:
            if a['id'] == affaire_id:
                affaire_courante = a
                break

    # Merge avg_pu_actualise depuis compute_ratios (SDO 1000, complexité 1.0)
    # Remplace pu_ht=None pour les articles sans affaire_id ni ratio_override
    try:
        ratios_ref = compute_ratios(1000.0, 1.0, 1.0, 1.0)
        for art in data['articles']:
            if not art.get('pu_ht') and art['id'] in ratios_ref:
                art['pu_ht'] = ratios_ref[art['id']].get('avg_pu_actualise') or 0
    except Exception:
        pass

    return render_template(
        'bibliotheque.html',
        affaires=data['affaires'],
        articles_json=json.dumps(data['articles'], ensure_ascii=False),
        sec_ratios_json=json.dumps(data.get('sec_ratios', {}), ensure_ascii=False),
        affaire_id=affaire_id,
        affaire_courante=affaire_courante,
    )


@app.route('/api/bibliotheque/save', methods=['POST'])
def bibliotheque_save():
    """Persiste les modifications inline de la bibliothèque (debounce 800 ms)."""
    data = None
    try:
        data    = request.get_json(force=True) or {}
        changes = data.get('changes', [])
        new_ids = models.save_bibliotheque_save(changes)
        return jsonify({'status': 'ok', 'saved': len(changes), 'new_ids': new_ids})
    except Exception as exc:
        # Reconstruit un aperçu du payload depuis le JSON parsé (si disponible)
        # pour éviter de consommer le stream deux fois
        payload_preview = str(data)[:600] if data else '(JSON non parsé)'
        logger.error(
            f"bibliotheque/save FAILED | payload={payload_preview} | {exc}"
        )
        logger.exception(exc)
        return jsonify({'status': 'error', 'code': 500, 'message': str(exc)}), 500


@app.route('/api/bibliotheque/article/delete', methods=['POST'])
def bibliotheque_article_delete():
    """Supprime ou masque un article de la bibliothèque."""
    data = None
    try:
        data      = request.get_json(force=True) or {}
        art_id    = data.get('id')
        is_custom = data.get('is_custom', False)
        if not art_id:
            return jsonify({'status': 'error', 'code': 400, 'message': 'id manquant'}), 400
        if is_custom:
            models.delete_custom_article(int(art_id))
            return jsonify({'status': 'ok', 'action': 'deleted'})
        else:
            models.hide_article(int(art_id))
            return jsonify({'status': 'ok', 'action': 'hidden'})
    except Exception as exc:
        payload_preview = str(data)[:300] if data else '(JSON non parsé)'
        logger.error(f"bibliotheque/article/delete FAILED | payload={payload_preview} | {exc}")
        logger.exception(exc)
        return jsonify({'status': 'error', 'code': 500, 'message': str(exc)}), 500


@app.route('/ratios')
def ratios_page():
    from scripts.rapport_ratios import compute_strategic_ratios
    conn = models.get_db()
    ratios_raw = compute_ratios(5000)
    conn.close()
    return render_template('index.html',
                           affaires=models.get_affaires(),
                           categories=models.get_categories())


@app.route('/import')
def import_page():
    projects   = models.get_projects_list()
    categories = models.get_categories()
    return render_template('import.html',
                           projects=projects,
                           categories=categories,
                           today=date.today().isoformat())


@app.route('/import/upload', methods=['POST'])
def import_upload():
    if 'file' not in request.files:
        flash('Aucun fichier sélectionné', 'error')
        return redirect(url_for('import_page'))

    f = request.files['file']
    if not f.filename:
        flash('Nom de fichier vide', 'error')
        return redirect(url_for('import_page'))

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ['.xlsx', '.xls', '.csv']:
        flash('Format non supporté (xlsx/xls/csv uniquement)', 'error')
        return redirect(url_for('import_page'))

    # Sauvegarde temporaire dans le dossier Devis entreprises
    devis_dir = os.path.join(PROJECT_DIR, 'Devis entreprises')
    os.makedirs(devis_dir, exist_ok=True)
    filepath  = os.path.join(devis_dir, f.filename)
    f.save(filepath)

    # Paramètres du formulaire
    name          = request.form.get('name') or os.path.splitext(f.filename)[0]
    devis_date    = request.form.get('devis_date') or date.today().isoformat()
    category_id   = request.form.get('category_id') or '1'
    sdo           = request.form.get('sdo') or '1000'
    coef_cfo      = request.form.get('coef_cfo') or '1.0'
    coef_cfa      = request.form.get('coef_cfa') or '1.0'
    coef_pv       = request.form.get('coef_pv')  or '1.0'
    total_ht_cell = request.form.get('total_ht_cell') or ''

    # Construction de la commande CLI
    cmd = [
        sys.executable,
        os.path.join(PROJECT_DIR, 'scripts', 'import_devis.py'),
        '--non-interactive',
        '--name',        name,
        '--date',        devis_date,
        '--category-id', category_id,
        '--sdo',         sdo,
        '--coef-cfo',    coef_cfo,
        '--coef-cfa',    coef_cfa,
        '--coef-pv',     coef_pv,
    ]
    if total_ht_cell:
        cmd += ['--total-ht-cell', total_ht_cell]

    # Patch : redirige le fichier vers DEVIS_FILE attendu par import_devis.py
    env = os.environ.copy()
    env['DEVIS_FILE_OVERRIDE'] = filepath

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            cwd=PROJECT_DIR, env=env,
            timeout=120, encoding='utf-8', errors='replace'
        )
        output  = result.stdout + result.stderr
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        output  = "Timeout — import trop long"
        success = False

    # Nettoyage des séquences ANSI (loguru colorize=True) pour affichage HTML.
    # Sans ça, les codes de couleur s'affichent en clair : "[32m19:34:09[0m | [1mINFO ..."
    import re as _re
    _ANSI_RE = _re.compile(r'\x1b\[[0-9;]*m|\x1b\[\d+m|\[[0-9;]+m|\[1m|\[0m')
    output = _ANSI_RE.sub('', output)

    return render_template('import_result.html',
                           success=success,
                           output=output,
                           filename=f.filename,
                           name=name)


# ─── Mapping manuel ───────────────────────────────────────────────────────────

@app.route('/mapping/<int:project_id>')
def mapping_page(project_id):
    from models import get_db
    conn = get_db()
    project = conn.execute(
        "SELECT * FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    conn.close()

    if not project:
        flash('Projet introuvable', 'error')
        return redirect(url_for('import_page'))

    all_lines     = models.get_all_mappable_lines(project_id)
    dpgf_articles = models.get_dpgf_articles_flat()

    return render_template('mapping.html',
                           project=dict(project),
                           lines=all_lines,
                           articles=dpgf_articles)


@app.route('/api/mapping/assign', methods=['POST'])
def mapping_assign():
    data            = request.get_json(force=True)
    line_id         = data.get('line_id')
    dpgf_article_id = data.get('dpgf_article_id')
    action          = data.get('action', 'assign')

    if action == 'unmapped':
        models.mark_unmapped(line_id)
        return jsonify({'status': 'ok', 'action': 'unmapped'})

    if not line_id or not dpgf_article_id:
        return jsonify({'error': 'line_id et dpgf_article_id requis'}), 400

    models.assign_mapping(line_id, dpgf_article_id)
    return jsonify({'status': 'ok', 'action': 'assigned'})


# ─── Suppression projet ──────────────────────────────────────────────────────

@app.route('/api/project/<int:project_id>/delete', methods=['POST'])
def project_delete(project_id):
    """Supprime un projet d'import (y compris si mapping en cours)."""
    ok = models.delete_project(project_id)
    if ok:
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'error', 'message': 'Projet introuvable'}), 404


# ─── Ratio overrides ──────────────────────────────────────────────────────────

@app.route('/api/ratio/correct', methods=['POST'])
def ratio_correct():
    data            = request.get_json(force=True)
    dpgf_article_id = data.get('dpgf_article_id')
    pu_override     = data.get('pu_override')
    raison          = data.get('raison', '')
    scope           = data.get('scope', 'affaire')  # 'affaire' | 'base'

    if scope == 'base' and dpgf_article_id and pu_override:
        models.save_ratio_override(int(dpgf_article_id), float(pu_override), raison)
        return jsonify({'status': 'ok', 'message': 'Base globale mise à jour'})

    return jsonify({'status': 'ok', 'message': 'Appliqué à cette affaire uniquement'})


# ─── API utilitaires ──────────────────────────────────────────────────────────

@app.route('/api/ratios')
def api_ratios():
    """Retourne les ratios calculés pour un SDO et des complexités donnés."""
    sdo  = float(request.args.get('sdo',  1000))
    ccfo = float(request.args.get('ccfo', 1.0))
    ccfa = float(request.args.get('ccfa', 1.0))
    cpv  = float(request.args.get('cpv',  1.0))
    ratios = compute_ratios(sdo, ccfo, ccfa, cpv)
    return jsonify(ratios)


@app.route('/api/projects')
def api_projects():
    return jsonify(models.get_projects_list())


# ─── Heartbeat & shutdown (fermeture auto quand navigateur ferme) ─────────────
#
# Principe :
#   - Le navigateur envoie un heartbeat toutes les 3 s vers /api/heartbeat
#   - Si le serveur ne reçoit plus rien pendant 15 s, il s'arrête tout seul
#   - À la fermeture de l'onglet, le JS envoie /api/shutdown → arrêt immédiat
#   - Un bouton "Quitter" dans l'UI envoie aussi /api/shutdown
#
# Grâce au STARTUP_GRACE, le serveur ne s'arrête PAS avant qu'un navigateur
# n'ait jamais ouvert l'app (utile en debug ou si le navigateur est lent).

_last_heartbeat     = None                  # None = aucun heartbeat reçu
_heartbeat_lock     = threading.Lock()
_shutdown_requested = threading.Event()

HEARTBEAT_TIMEOUT_S = 60   # arrêt si pas de heartbeat depuis X s (60 s : couvre le throttling navigateur en arrière-plan)
STARTUP_GRACE_S     = 90   # délai avant d'exiger le 1er heartbeat


def _watchdog_loop():
    """Thread qui tue le serveur si le navigateur ne donne plus signe de vie."""
    start_time = time.time()
    while not _shutdown_requested.is_set():
        time.sleep(2)
        with _heartbeat_lock:
            last = _last_heartbeat
        now = time.time()
        if last is None:
            # Aucun heartbeat reçu encore : on attend la grace period
            if now - start_time > STARTUP_GRACE_S:
                print(f"[WATCHDOG] Aucun heartbeat reçu en {STARTUP_GRACE_S}s — arrêt.", flush=True)
                os._exit(0)
        else:
            elapsed = now - last
            if elapsed > HEARTBEAT_TIMEOUT_S:
                print(f"[WATCHDOG] Pas de heartbeat depuis {elapsed:.0f}s — arrêt.", flush=True)
                os._exit(0)


@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    """Reçoit les battements de cœur du navigateur (toutes les 3 s)."""
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    return ('', 204)


@app.route('/api/shutdown', methods=['POST'])
def api_shutdown():
    """Arrête proprement le serveur (appelé à la fermeture de l'onglet ou via le bouton Quitter)."""
    def _kill_after_response():
        time.sleep(0.3)          # laisse le temps à la réponse HTTP de partir
        print("[SHUTDOWN] Arrêt demandé par le navigateur.", flush=True)
        _shutdown_requested.set()
        os._exit(0)
    threading.Thread(target=_kill_after_response, daemon=True).start()
    return ('', 204)


def _initialize_affaire_lines(affaire_id: int, affaire: dict):
    """Pré-remplit les 285 lignes DPGF à la création d'une affaire.

    Sprint 7 (rév. Eric) :
      - Lignes **SURFACIQUE / m²** (têtes de sous-chapitres) → qty = SDO,
        pu = ratio €/m² (prix unitaire par m²). Total = SDO × €/m².
      - Autres articles (u / ens / ml / kWp) → qty = 0 (saisie utilisateur).
      - Têtes de chapitre : qty=1 + mode Macro (table ``affaire_chapter_settings``).
    """
    target_sdo = float(affaire.get('surface_sdo', 1000))
    tree = get_dpgf_tree_with_ratios(
        target_sdo            = target_sdo,
        target_complexity_cfo = float(affaire.get('coef_complexity_cfo', 1.0)),
        target_complexity_cfa = float(affaire.get('coef_complexity_cfa', 1.0)),
        target_complexity_pv  = float(affaire.get('coef_complexity_pv',  1.0)),
    )
    lines = []
    for chapter in tree:
        for section in chapter.get('sections', []):
            for art in section.get('articles', []):
                unit       = (art.get('unit') or '').lower()
                ratio_type = art.get('ratio_type') or ''
                is_surface = (unit in ('m²', 'm2')) or (ratio_type == 'SURFACIQUE')

                total_ref  = art.get('total') or 0                  # = qty_estimee × unit_price
                pu_ref     = art.get('unit_price') or 0

                if is_surface and target_sdo > 0:
                    # Ligne m² : qty = SDO, pu = ratio €/m² (= total théorique / SDO)
                    qty     = target_sdo
                    pu      = total_ref / target_sdo if total_ref else pu_ref
                    total   = qty * pu
                else:
                    # Autres : qty 0, pu pré-rempli depuis le ratio comme aide à la saisie
                    qty     = 0
                    pu      = pu_ref
                    total   = 0

                lines.append({
                    'dpgf_article_id':   art['id'],
                    'quantity':          qty,
                    'quantity_source':   'ratio',
                    'unit_price_ht':     pu,
                    'unit_price_source': 'ratio',
                    'total_ht':          total,
                    'is_included':       True,
                    'ratio_ref':         art.get('avg_pu_actualise') or pu_ref,
                })
    models.save_affaire_lines(affaire_id, lines)
    # Têtes de chapitre : qty=1, mode Macro actif (ratio €/m² propre du chapitre)
    models.init_chapter_settings(affaire_id, tree)


if __name__ == '__main__':
    print("=" * 60)
    print(" Estimation Élec — Application Web Locale")
    print(" http://localhost:5000")
    print("=" * 60, flush=True)

    # Démarre le watchdog de fermeture auto (heartbeat navigateur)
    threading.Thread(target=_watchdog_loop, daemon=True).start()

    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
