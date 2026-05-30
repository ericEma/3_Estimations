"""
app.py — Application Flask — Estimation Élec
Sprint 4 : Full Stack Local

Lancement :
    python app.py
    → http://localhost:8080  (PORT env, défaut 8080)
"""

import os
import sys
import json
import time
import threading
import subprocess
import tempfile
import re
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
from flask.json.provider import DefaultJSONProvider


class Utf8JSONProvider(DefaultJSONProvider):
    """JSON API en UTF-8 lisible (pas d'échappement \\u00e8 dans les réponses)."""

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("ensure_ascii", False)
        return super().dumps(obj, **kwargs)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

import models
import db_profiles
from db_profiles import PROFILE_LABELS, normalize_profile, normalize_profile_filter
from scripts.engine_ratios import get_dpgf_tree_with_ratios, compute_ratios
from scripts.engine_bibliotheque_ratios import compute_bibliotheque_lot_totals
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
app.json = Utf8JSONProvider(app)
app.secret_key = 'elec-estim-2026-local'
app.config['UPLOAD_FOLDER']   = os.path.join(PROJECT_DIR, 'uploads')
app.config['EXPORT_FOLDER']   = os.path.join(PROJECT_DIR, 'exports')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB

for folder in [app.config['UPLOAD_FOLDER'], app.config['EXPORT_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# Initialise les tables web au démarrage
models.ensure_app_tables()


def _profil_qs(affaire=None, profil=None):
    p = profil or (affaire or {}).get('price_profile')
    if p:
        return {'profil': normalize_profile(p)}
    return {}


def _request_profil():
    return request.args.get('profil') or request.form.get('profil')


def _affaire_or_404(affaire_id: int):
    profil = _request_profil()
    affaire = models.get_affaire(affaire_id, profile=profil)
    if not affaire:
        return None, None
    return affaire, affaire.get('price_profile') or 'autres'


@app.context_processor
def inject_global_nav():
    """Menu latéral commun + date MAJ base de prix (modification PU)."""
    return {
        'nav_affaires': models.get_affaires(),
        'base_price_last_updated': models.get_base_price_last_updated(),
        'profile_labels': PROFILE_LABELS,
    }


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
    profile_filter = normalize_profile_filter(request.args.get('profil', 'tous'))
    affaires   = models.get_affaires(profile_filter)
    categories = models.get_categories()
    return render_template('index.html',
                           affaires=affaires,
                           categories=categories,
                           profile_filter=profile_filter,
                           profile_labels=PROFILE_LABELS)


def _preview_maj_date(profile=None):
    try:
        path = db_profiles.get_db_path(normalize_profile(profile) if profile else None)
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime('%d-%m-%Y')
    except Exception:
        return None


def compute_affaire_preview_estimation(
    surface_sdo,
    puissance_pv_kwc,
    taux_phase,
    taux_incertitude,
    coef_risque,
    coef_complexity_cfo,
    coef_complexity_cfa,
    pv_system_type,
    ratio_global_cfo_m2=None,
    ratio_global_cfa_m2=None,
    ratio_global_pv_kwc=None,
    price_profile=None,
):
    """Estimation prévisionnelle — ratios fiche ou Bibliothèque DPGF (pu_ht_ref).

    Prix CFO/CFA = ratio global × SDO × complexité lot × provisions.
    Prix PV = ratio global × kWc × coef type système × provisions.
    """
    biblio = compute_bibliotheque_lot_totals(
        surface_sdo, puissance_pv_kwc, profile=price_profile
    )
    sdo = biblio['surface_sdo']
    kwc = biblio['puissance_pv_kwc']
    ratio_cfo = models.optional_positive_float(ratio_global_cfo_m2) or biblio['ratio_m2_cfo']
    ratio_cfa = models.optional_positive_float(ratio_global_cfa_m2) or biblio['ratio_m2_cfa']
    ratio_pv = models.optional_positive_float(ratio_global_pv_kwc) or biblio['ratio_kwc_pv']
    base = {
        'CFO': ratio_cfo * sdo,
        'CFA': ratio_cfa * sdo,
        'PV': ratio_pv * kwc,
    }
    ccfo = models.snap_complexity_coef(coef_complexity_cfo)
    ccfa = models.snap_complexity_coef(coef_complexity_cfa)
    prov = (
        (1.0 + float(taux_phase or 0) / 100.0)
        * (1.0 + float(taux_incertitude or 0) / 100.0)
        * (1.0 + float(coef_risque or 0) / 100.0)
    )
    pv_sys = models.pv_system_coef(pv_system_type)
    prix_cfo = base['CFO'] * ccfo * prov
    prix_cfa = base['CFA'] * ccfa * prov
    prix_pv = base['PV'] * pv_sys * prov
    total = prix_cfo + prix_cfa + prix_pv
    return {
        'prix_cfo': round(prix_cfo, 0),
        'prix_cfa': round(prix_cfa, 0),
        'prix_pv': round(prix_pv, 0),
        'prix_total': round(total, 0),
        'ratio_m2_cfo': round(ratio_cfo, 2),
        'ratio_m2_cfa': round(ratio_cfa, 2),
        'ratio_kwc_pv': round(ratio_pv, 4),
        'coef_pv_system': pv_sys,
        'maj_date_bdd': _preview_maj_date(price_profile),
    }


def _compute_preview_context(affaire=None, price_profile=None):
    """Contexte initial page Création / Édition (date MAJ BDD)."""
    surface = 1000
    kwc = 100
    prof = price_profile
    if affaire:
        surface = affaire.get('surface_sdo') or surface
        kwc = affaire.get('puissance_pv_kwc') or kwc
        prof = affaire.get('price_profile') or prof
    biblio = compute_bibliotheque_lot_totals(surface, kwc, profile=prof)
    return {
        'maj_date_bdd': _preview_maj_date(prof),
        'preview_ratio_global_cfo_m2': (
            models.optional_positive_float(affaire.get('ratio_global_cfo_m2')) if affaire else None
        ) or biblio['ratio_m2_cfo'],
        'preview_ratio_global_cfa_m2': (
            models.optional_positive_float(affaire.get('ratio_global_cfa_m2')) if affaire else None
        ) or biblio['ratio_m2_cfa'],
        'preview_ratio_global_pv_kwc': (
            models.optional_positive_float(affaire.get('ratio_global_pv_kwc')) if affaire else None
        ) or biblio['ratio_kwc_pv'],
    }


@app.route('/api/affaire/preview_estimation')
def api_affaire_preview_estimation():
    """Calcul temps réel de l'estimation prévisionnelle (fiche affaire)."""
    try:
        payload = compute_affaire_preview_estimation(
            surface_sdo=request.args.get('surface_sdo', 1000),
            puissance_pv_kwc=request.args.get('puissance_pv_kwc', 100),
            taux_phase=request.args.get('taux_phase', 3),
            taux_incertitude=request.args.get('taux_incertitude', 3),
            coef_risque=request.args.get('coef_risque', 1),
            coef_complexity_cfo=request.args.get('coef_complexity_cfo', 1),
            coef_complexity_cfa=request.args.get('coef_complexity_cfa', 1),
            pv_system_type=request.args.get('pv_system_type', 'toiture'),
            ratio_global_cfo_m2=request.args.get('ratio_global_cfo_m2'),
            ratio_global_cfa_m2=request.args.get('ratio_global_cfa_m2'),
            ratio_global_pv_kwc=request.args.get('ratio_global_pv_kwc'),
            price_profile=(
                models.resolve_profile_from_category_id(request.args.get('category_id'))
                if request.args.get('category_id')
                else normalize_profile(request.args.get('profil')) if request.args.get('profil') else None
            ),
        )
        return jsonify({'ok': True, **payload})
    except Exception as exc:
        logger.exception('preview_estimation failed')
        return jsonify({'ok': False, 'error': str(exc)}), 500


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
            'coef_complexity_cfo': models.snap_complexity_coef(request.form.get('coef_complexity_cfo')),
            'coef_complexity_cfa': models.snap_complexity_coef(request.form.get('coef_complexity_cfa')),
            'coef_complexity_pv':  1.0,
            'ratio_global_cfo_m2': request.form.get('ratio_global_cfo_m2'),
            'ratio_global_cfa_m2': request.form.get('ratio_global_cfa_m2'),
            'ratio_global_pv_kwc': request.form.get('ratio_global_pv_kwc'),
            'coef_risque':         float(request.form.get('coef_risque') or 1.0),
            'kva_cible':           float(request.form.get('kva_cible') or 800.0),
            'puissance_pv_kwc':    float(request.form.get('puissance_pv_kwc') or 100.0),
            'pv_system_type':      request.form.get('pv_system_type'),
            'phase_etude':         request.form.get('phase_etude') or 'APD',
            'taux_phase':          float(request.form.get('taux_phase') or 3.0),
            'taux_incertitude':    float(request.form.get('taux_incertitude') or 3.0),
            'notes':               request.form.get('notes'),
        }
        affaire_id, profil = models.create_affaire(data)
        return redirect(url_for(
            'affaire_estimation', affaire_id=affaire_id, profil=profil
        ))

    return render_template('affaire_new.html',
                           categories=categories,
                           **_compute_preview_context())


@app.route('/affaire/<int:affaire_id>/edit', methods=['GET', 'POST'])
def affaire_edit(affaire_id):
    affaire, profil = _affaire_or_404(affaire_id)
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
            'coef_complexity_cfo': models.snap_complexity_coef(request.form.get('coef_complexity_cfo')),
            'coef_complexity_cfa': models.snap_complexity_coef(request.form.get('coef_complexity_cfa')),
            'coef_complexity_pv':  1.0,
            'ratio_global_cfo_m2': request.form.get('ratio_global_cfo_m2'),
            'ratio_global_cfa_m2': request.form.get('ratio_global_cfa_m2'),
            'ratio_global_pv_kwc': request.form.get('ratio_global_pv_kwc'),
            'coef_risque':         float(request.form.get('coef_risque') or 0.0),
            'kva_cible':           float(request.form.get('kva_cible') or 800.0),
            'puissance_pv_kwc':    float(request.form.get('puissance_pv_kwc') or 100.0),
            'pv_system_type':      request.form.get('pv_system_type'),
            'phase_etude':         request.form.get('phase_etude') or 'APD',
            'taux_phase':          float(request.form.get('taux_phase') or 3.0),
            'taux_incertitude':    float(request.form.get('taux_incertitude') or 3.0),
            'notes':               request.form.get('notes'),
            'statut':              affaire.get('statut', 'brouillon'),
        }
        models.update_affaire(affaire_id, data)
        return redirect(url_for(
            'affaire_estimation', affaire_id=affaire_id, profil=profil
        ))

    return render_template('affaire_new.html',
                           categories=categories,
                           affaire=affaire,
                           edit_mode=True,
                           current_affaire_id=affaire_id,
                           price_profile=profil,
                           **_compute_preview_context(affaire, profil))


@app.route('/affaire/<int:affaire_id>')
def affaire_view(affaire_id):
    affaire, profil = _affaire_or_404(affaire_id)
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
                           current_affaire_id=affaire_id,
                           price_profile=profil,
                           tree=tree,
                           totals=totals,
                           categories=categories)


@app.route('/affaire/<int:affaire_id>/estimation')
def affaire_estimation(affaire_id):
    """Page saisie « double calque » : référentiel (lecture seule) + estimation éditable."""
    affaire, profil = _affaire_or_404(affaire_id)
    if not affaire:
        flash('Affaire introuvable', 'error')
        return redirect(url_for('index'))

    models.ensure_estimation_snapshot(affaire_id, profil)

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
        current_affaire_id=affaire_id,
        price_profile=profil,
        catalog_rows=catalog,
        custom_rows=customs,
        totals=totals,
        chapter_state=chapter_state,
        section_state=section_state,
    )


@app.route('/api/affaire/<int:affaire_id>/estimation/promote', methods=['POST'])
def affaire_estimation_promote(affaire_id):
    """Promotion section / article affaire-only → base de prix."""
    if not models.get_affaire(affaire_id):
        return jsonify({'ok': False, 'message': 'Affaire introuvable'}), 404
    data = request.get_json(force=True) or {}
    action = (data.get('action') or '').strip()
    try:
        from estimation_promote import handle_promote_action

        out = handle_promote_action(affaire_id, action, data)
        if out.get('status') != 'ok':
            return jsonify({'ok': False, 'message': out.get('message', 'Erreur')}), 400
        return jsonify({'ok': True, **out})
    except Exception as exc:
        logger.exception('estimation/promote failed')
        return jsonify({'ok': False, 'message': str(exc)}), 500


@app.route('/api/affaire/<int:affaire_id>/estimation/layout', methods=['POST'])
def affaire_estimation_layout(affaire_id):
    """Sections/articles affaire-only + réordonnancement (Sprint 11)."""
    if not models.get_affaire(affaire_id):
        return jsonify({'ok': False, 'message': 'Affaire introuvable'}), 404
    data = request.get_json(force=True) or {}
    action = (data.get('action') or '').strip()
    try:
        from estimation_layout import handle_layout_action

        out = handle_layout_action(affaire_id, action, data)
        if out.get('status') != 'ok':
            return jsonify({'ok': False, 'message': out.get('message', 'Erreur')}), 400
        return jsonify({'ok': True, **out})
    except Exception as exc:
        logger.exception('estimation/layout failed')
        return jsonify({'ok': False, 'message': str(exc)}), 500


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
    affaire = models.get_affaire(affaire_id)
    if affaire and ('surface_sdo' in data or 'puissance_pv_kwc' in data):
        try:
            sdo = float(affaire.get('surface_sdo') or 0)
            kwc = float(affaire.get('puissance_pv_kwc') or 100)
            if affaire.get('estimation_initialized_at'):
                synced = models.sync_estimation_macro_divisors(affaire_id, sdo, kwc)
            elif 'surface_sdo' in data:
                synced = models.batch_sync_estimation_m2_quantities(affaire_id, sdo)
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
    prof_qs = normalize_profile(_request_profil()) if _request_profil() else None
    affaire_courante = None
    if affaire_id:
        aff_row = models.get_affaire(affaire_id, profile=prof_qs)
        if aff_row:
            affaire_courante = aff_row
            biblio_profile = aff_row.get('price_profile') or db_profiles.DEFAULT_PROFILE
        else:
            biblio_profile = prof_qs or db_profiles.DEFAULT_PROFILE
    else:
        biblio_profile = prof_qs or db_profiles.DEFAULT_PROFILE

    data = models.get_bibliotheque_data(affaire_id, profile=biblio_profile)
    biblio_profile = data.get('price_profile') or biblio_profile
    if affaire_id and not affaire_courante:
        for a in data['affaires']:
            if a['id'] == affaire_id:
                affaire_courante = a
                break

    # Merge avg_pu_actualise depuis compute_ratios (SDO 1000, complexité 1.0)
    # Repli uniquement si pu_ht **référentiel** absent (NULL) — pas si PU=0 (référent valide).
    try:
        ratios_ref = compute_ratios(1000.0, 1.0, 1.0, 1.0)
        # compute_ratios : legacy DB path si profil non branché dans engine_ratios
        for art in data['articles']:
            if art.get('pu_ht') is None and art['id'] in ratios_ref:
                art['pu_ht'] = ratios_ref[art['id']].get('avg_pu_actualise') or 0
    except Exception:
        pass

    articles = data['articles']
    nb_articles = len(articles)
    nb_chapitres = len({a['chapter'] for a in articles if a.get('chapter')})
    nb_sections  = len({(a['chapter'], a['section']) for a in articles if a.get('section')})

    return render_template(
        'bibliotheque.html',
        affaires=data['affaires'],
        articles_json=json.dumps(articles, ensure_ascii=False),
        sec_ratios_json=json.dumps(data.get('sec_ratios', {}), ensure_ascii=False),
        affaire_id=affaire_id,
        current_affaire_id=affaire_id,
        affaire_courante=affaire_courante,
        price_profile=biblio_profile,
        profile_labels=PROFILE_LABELS,
        nb_articles=nb_articles,
        nb_chapitres=nb_chapitres,
        nb_sections=nb_sections,
    )


@app.route('/api/bibliotheque/save', methods=['POST'])
def bibliotheque_save():
    """Persiste les modifications inline de la bibliothèque (debounce 800 ms)."""
    data = None
    try:
        data    = request.get_json(force=True) or {}
        changes = data.get('changes', [])
        prof = data.get('profil') or _request_profil()
        new_ids = models.save_bibliotheque_save(changes, profile=prof)
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
        prof = data.get('profil') or _request_profil()
        if is_custom:
            models.delete_custom_article(int(art_id), profile=prof)
            return jsonify({'status': 'ok', 'action': 'deleted'})
        else:
            models.hide_article(int(art_id), profile=prof)
            return jsonify({'status': 'ok', 'action': 'hidden'})
    except Exception as exc:
        payload_preview = str(data)[:300] if data else '(JSON non parsé)'
        logger.error(f"bibliotheque/article/delete FAILED | payload={payload_preview} | {exc}")
        logger.exception(exc)
        return jsonify({'status': 'error', 'code': 500, 'message': str(exc)}), 500


@app.route('/ratios')
def ratios_page():
    """Ancienne route — redirection vers Statistiques."""
    return redirect(url_for('statistiques_page'))


@app.route('/statistiques')
def statistiques_page():
    """Tableaux de bord ratios / typologie bâtiment (en cours de développement)."""
    return render_template('statistiques.html')


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
    env['ESTIMATION_PROFILE'] = models.resolve_profile_from_category_id(category_id)

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
    import re as _re
    _ANSI_RE = _re.compile(r'\x1b\[[0-9;]*m|\x1b\[\d+m|\[[0-9;]+m|\[1m|\[0m')
    output = _ANSI_RE.sub('', output)

    # Dernier projet inséré (même fichier SQLite que le subprocess, cwd=PROJECT_DIR)
    project_id_created = None
    import_profile = env.get('ESTIMATION_PROFILE', db_profiles.DEFAULT_PROFILE)
    if success:
        conn = models.get_db(import_profile)
        try:
            row = conn.execute(
                "SELECT id FROM projects ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                project_id_created = int(row["id"])
        finally:
            conn.close()

    # Redirection directe vers le cockpit Matching si import OK
    if success and project_id_created:
        return redirect(url_for(
            'matching_view',
            project_id=project_id_created,
            profil=import_profile,
        ))

    return render_template('import_result.html',
                           success=success,
                           output=output,
                           filename=f.filename,
                           name=name,
                           project_id=project_id_created)


# ─── Mapping manuel ───────────────────────────────────────────────────────────

@app.route('/mapping/<int:project_id>')
def mapping_page(project_id):
    from models import get_db
    conn = get_db(project_id=project_id)
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
# ─── Matching Cockpit — Lot 3 ────────────────────────────────────────────────

def _apply_matching_line_weighted_pricing(r: dict, conn, devis_date) -> None:
    """Remplit weighted_price, wp_tooltip, ecart_pct, wp_manual (dict ligne + join base)."""
    from utils import calculate_weighted_price, get_effective_date

    computed_wp = None
    computed_tt = None

    if r.get('dpgf_article_id') and r.get('unit_price_ht') and r.get('base_pu'):
        eff_date = get_effective_date(conn, r['dpgf_article_id'])
        wp = calculate_weighted_price(
            base_price=r['base_pu'],
            base_date=eff_date,
            devis_price=r['unit_price_ht'],
            devis_date=devis_date,
        )
        computed_wp = wp.get('weighted_price')
        computed_tt = wp
    elif (
        r.get('dpgf_article_id')
        and r.get('unit_price_ht')
        and not (r.get('base_pu') or 0)
        and (r.get('mapping_score') or 0) >= 80
        and (r.get('mapping_status') in ('auto', 'manual'))
    ):
        computed_wp = r.get('unit_price_ht')
        computed_tt = {
            'confidence': 'HIGH',
            'mode': 'devis_only',
            'note': 'PU base manquant → affichage PU devis',
            'base_actualized': None,
            'devis_actualized': r.get('unit_price_ht'),
            'base_age_years': None,
            'devis_age_years': None,
            'base_weight': None,
            'devis_weight': None,
        }

    overr = r.get('weighted_price_override')
    if overr is not None and overr != '':
        try:
            ow = round(float(overr), 2)
        except (TypeError, ValueError):
            ow = None
        if ow is not None and ow >= 0 and ow == ow:  # NaN guard
            r['weighted_price'] = ow
            r['wp_manual'] = True
            tt = dict(computed_tt) if computed_tt else {
                'confidence': 'NONE',
                'base_actualized': None,
                'devis_actualized': None,
                'base_age_years': None,
                'devis_age_years': None,
                'base_weight': None,
                'devis_weight': None,
            }
            tt['manual_override'] = True
            tt['computed_weighted_price'] = computed_wp
            prev_note = tt.get('note') or ''
            tt['note'] = (prev_note + (' · ' if prev_note else '')) + 'PU calculé saisi manuellement.'
            r['wp_tooltip'] = tt
            if r.get('unit_price_ht'):
                r['ecart_pct'] = round(
                    (ow - r['unit_price_ht']) / r['unit_price_ht'] * 100, 1
                )
            else:
                r['ecart_pct'] = None
            return

    r['weighted_price'] = computed_wp
    r['wp_manual'] = False
    r['wp_tooltip'] = computed_tt
    r['ecart_pct'] = None
    if computed_wp and r.get('unit_price_ht'):
        r['ecart_pct'] = round(
            (computed_wp - r['unit_price_ht']) / r['unit_price_ht'] * 100, 1
        )


@app.route('/matching')
@app.route('/matching/<int:project_id>')
def matching_view(project_id=None):
    projects   = models.get_projects_list()
    categories = models.get_categories()
    project    = None
    price_profile = normalize_profile(_request_profil()) if _request_profil() else None
    if project_id:
        price_profile = price_profile or db_profiles.find_project_profile(project_id)
        conn = models.get_db(project_id=project_id)
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        conn.close()
        project = dict(row) if row else None
    return render_template('matching_view.html',
                           projects=projects,
                           categories=categories,
                           project=project,
                           project_id=project_id,
                           price_profile=price_profile or db_profiles.DEFAULT_PROFILE,
                           today=date.today().isoformat())


@app.route('/api/matching/<int:project_id>/data')
def matching_data(project_id):
    conn = models.get_db(project_id=project_id)
    try:
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({'error': 'Projet introuvable'}), 404
        project = dict(project)
        devis_date = project.get('devis_date')
        sdo        = project.get('surface_sdo') or 1000.0

        rows = conn.execute("""
            SELECT dl.id, dl.original_designation, dl.unit, dl.quantity,
                   dl.unit_price_ht, dl.total_ht, dl.mapping_status,
                   dl.mapping_score, dl.row_type, dl.context_path, dl.lot,
                   dl.dpgf_article_id, dl.weighted_price_override,
                   da.designation  AS base_designation,
                   da.pu_ht_ref    AS base_pu,
                   da.unit         AS base_unit,
                   da.last_updated AS base_last_updated,
                   da.chapter      AS base_chapter,
                   da.section      AS base_section
            FROM devis_lines dl
            LEFT JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
            WHERE dl.project_id = ?
            ORDER BY dl.id
        """, (project_id,)).fetchall()

        # ── Groupe par chapitre / section ────────────────────────────────────
        chapters_order = []
        chapters_map   = {}

        def _derive_lot(chapter_txt):
            c = (chapter_txt or '').lower()
            if 'faible' in c or 'cfa' in c or 'ssi' in c:
                return 'CFA'
            if 'photo' in c or 'pv' in c:
                return 'PV'
            return 'CFO'

        def _is_excel_structure_meta_row(rec: dict) -> bool:
            """Lignes Excel d'en-tête (Nature « Titre » + type ratio sans montant) — pas des postes devis.

            PSA : Nature « Article » désigne une ligne de données ; le type ratio (Unitaire/Surfacique)
            est souvent importé dans original_designation pour toutes les lignes — ne pas l'utiliser
            seul comme signal méta pour unit=Article (sinon cockpit vide).
            """
            if rec.get("row_type") != "article":
                return False
            u = (rec.get("unit") or "").strip().lower()
            if u != "titre":
                return False
            d = (rec.get("original_designation") or "").strip().lower()
            ratio_only = frozenset(("surfacique", "unitaire"))
            if (rec.get("unit_price_ht") or 0) or (rec.get("total_ht") or 0):
                return False
            if d in ratio_only or not d:
                return True
            return False

        for row in rows:
            r = dict(row)
            if _is_excel_structure_meta_row(r):
                continue
            ctx    = (r.get('context_path') or '').split(' > ')
            chap   = ctx[0].strip() if ctx else '—'
            sec    = ctx[1].strip() if len(ctx) > 1 else '—'
            lot    = (r.get('lot') or _derive_lot(chap)).upper()
            r['lot'] = lot

            _apply_matching_line_weighted_pricing(r, conn, devis_date)

            if chap not in chapters_map:
                chapters_order.append(chap)
                chapters_map[chap] = {}
            if sec not in chapters_map[chap]:
                chapters_map[chap][sec] = []
            chapters_map[chap][sec].append(r)

        # ── Calcul ratios par section (RÈGLE D'OR : inclut lignes exclues) ──
        result = []
        for chap in chapters_order:
            chap_lot     = _derive_lot(chap)
            chap_sections = []
            for sec, lines in chapters_map[chap].items():
                total_ht_sec = sum(
                    (l.get('total_ht') or 0)
                    for l in lines
                    if l['row_type'] == 'article' and not _is_excel_structure_meta_row(l)
                )
                ratio_m2 = round(total_ht_sec / sdo, 2) if sdo > 0 else 0
                chap_sections.append({
                    'name':     sec,
                    'lines':    lines,
                    'total_ht': round(total_ht_sec, 2),
                    'ratio_m2': ratio_m2,
                })
            result.append({'name': chap, 'lot': chap_lot, 'sections': chap_sections})

        return jsonify({'project': project, 'chapters': result, 'sdo': sdo})
    finally:
        conn.close()


@app.route('/api/matching/line/<int:line_id>/candidates')
def matching_line_candidates(line_id):
    import json
    import time

    from engine_matching import clean_designation_radical, find_best_match, resolve_dpgf_section

    conn = models.get_db(line_id=line_id)
    try:
        line = conn.execute(
            """
            SELECT dl.original_designation, dl.unit, dl.unit_price_ht, dl.lot, dl.context_path,
                   dl.dpgf_article_id,
                   da.chapter AS base_chapter, da.section AS base_section
            FROM devis_lines dl
            LEFT JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
            WHERE dl.id = ?
            """,
            (line_id,),
        ).fetchone()
        if not line:
            return jsonify({'error': 'Ligne introuvable'}), 404
        lot   = (line['lot'] or 'CFO').upper()
        ctx   = (line['context_path'] or '').split(' > ')
        dch   = ctx[0].strip() if ctx else ''
        dsec  = ctx[1].strip() if len(ctx) > 1 else ''
        raw_des = line['original_designation'] or ''
        candidates = find_best_match(
            conn,
            raw_des,
            lot,
            top_n=5,
            devis_chapter=dch or None,
            devis_section=dsec or None,
        )
        n_after_find = len(candidates)
        resolved_sec = None
        filter_mode = "none"
        # Filtrage par section DPGF : résolution fuzzy du libellé devis → section référentiel
        if dsec:
            resolved_sec = resolve_dpgf_section(conn, lot, dsec)
            if resolved_sec:
                filter_mode = "resolved"
                resolved_low = resolved_sec.strip().lower()
                candidates = [
                    c for c in candidates
                    if (c.get("section") or "").strip().lower() == resolved_low
                ]
            else:
                filter_mode = "no_resolve_skip_filter"
        # #region agent log
        _dbg_path = os.environ.get("ESTIMATION_DEBUG_LOG") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "debug-b2b456.log"
        )
        try:
            with open(_dbg_path, "a", encoding="utf-8") as _df:
                _df.write(
                    json.dumps(
                        {
                            "sessionId": "b2b456",
                            "hypothesisId": "H_match",
                            "location": "app.py:matching_line_candidates",
                            "message": "section filter",
                            "timestamp": int(time.time() * 1000),
                            "data": {
                                "line_id": line_id,
                                "context_path": line["context_path"],
                                "dch": dch,
                                "dsec": dsec,
                                "resolved_sec": resolved_sec,
                                "filter_mode": filter_mode,
                                "n_after_find": n_after_find,
                                "n_final": len(candidates),
                                "candidate_sections": [
                                    (c.get("section") or "") for c in candidates[:8]
                                ],
                                "mapped_base_section": line["base_section"],
                                "mapped_article_id": line["dpgf_article_id"],
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass
        # #endregion
        return jsonify({
            'candidates': candidates,
            'cleaned_designation': clean_designation_radical(raw_des),
            'line': {
                'lot': lot,
                'unit': line['unit'],
                'unit_price_ht': line['unit_price_ht'],
                'context_path': line['context_path'],
                'chapter': dch or None,
                'section': dsec or None,
                'base_chapter': line['base_chapter'] or None,
                'base_section': line['base_section'] or None,
            }
        })
    finally:
        conn.close()


@app.route('/api/matching/line/<int:line_id>/section_articles')
def matching_line_section_articles(line_id):
    """Retourne tous les articles base d'un chapitre/section (détectés ou choisis) pour la ligne devis."""
    conn = models.get_db(line_id=line_id)
    try:
        line = conn.execute(
            "SELECT lot, context_path FROM devis_lines WHERE id=?",
            (line_id,),
        ).fetchone()
        if not line:
            return jsonify({'error': 'Ligne introuvable'}), 404

        lot_override = (request.args.get("lot") or "").strip().upper()
        lot = lot_override if lot_override in ("CFO", "CFA", "PV") else (line['lot'] or 'CFO').upper()
        ctx = (line['context_path'] or '').split(' > ')
        dch = ctx[0].strip() if ctx else ''
        dsec = ctx[1].strip() if len(ctx) > 1 else ''

        def _norm_ctx_part(s: str) -> str:
            s = (s or "").strip()
            # ex: "2.1 — Installation de chantier" -> "Installation de chantier"
            s = re.sub(r"^\s*\d+(?:\.\d+)*\s*[–—-]\s*", "", s)
            s = re.sub(r"\s{2,}", " ", s)
            return s.strip()

        # Chapitre/section forcés par l'utilisateur (parcours manuel)
        ch_q = request.args.get("chapter")
        sec_q = request.args.get("section")

        dch_n = _norm_ctx_part(ch_q) if ch_q else _norm_ctx_part(dch)
        dsec_n = _norm_ctx_part(sec_q) if sec_q else _norm_ctx_part(dsec)

        if not dch_n or not dsec_n:
            return jsonify({'articles': [], 'chapter': dch_n or None, 'section': dsec_n or None, 'lot': lot})

        rows = conn.execute(
            """
            SELECT id, designation, unit, pu_ht_ref, chapter, section, lot
            FROM dpgf_articles
            WHERE row_type='article'
              AND lot=?
              AND LOWER(TRIM(chapter))=LOWER(TRIM(?))
              AND LOWER(TRIM(section))=LOWER(TRIM(?))
              AND (is_hidden IS NULL OR is_hidden=0)
            ORDER BY row_order, id
            """,
            (lot, dch_n, dsec_n),
        ).fetchall()

        def _breadcrumb(ch, sec):
            parts = [p.strip() for p in [ch or "", sec or ""] if p and p.strip()]
            return " ➔ ".join(parts)

        articles = []
        for r in rows:
            rr = dict(r)
            des = rr.get("designation") or ""
            ch = rr.get("chapter")
            sec = rr.get("section")
            articles.append({
                "article_id": rr.get("id"),
                "designation": des,
                "unit": rr.get("unit") or "",
                "pu_ht_ref": rr.get("pu_ht_ref") or 0.0,
                "score": 0.0,
                "match_type": "browse",
                "chapter": ch,
                "section": sec,
                "lot": (rr.get("lot") or lot).upper(),
                "breadcrumb": _breadcrumb(ch, sec),
                "path": " ➔ ".join([p for p in [ch, sec, des] if p]),
            })

        return jsonify({'articles': articles, 'chapter': dch_n, 'section': dsec_n, 'lot': lot})
    finally:
        conn.close()


@app.route('/api/matching/line/<int:line_id>/browse_options')
def matching_line_browse_options(line_id):
    """Retourne les options de sous-chapitres (avec chapitre) pour le parcours manuel."""
    conn = models.get_db(line_id=line_id)
    try:
        line = conn.execute(
            "SELECT lot, context_path FROM devis_lines WHERE id=?",
            (line_id,),
        ).fetchone()
        if not line:
            return jsonify({'error': 'Ligne introuvable'}), 404

        lot_override = (request.args.get("lot") or "").strip().upper()
        lot = lot_override if lot_override in ("CFO", "CFA", "PV") else (line['lot'] or 'CFO').upper()
        ctx = (line['context_path'] or '').split(' > ')
        devis_ch = (ctx[0].strip() if ctx else '') or None
        devis_sec = (ctx[1].strip() if len(ctx) > 1 else '') or None

        def _norm(s: str | None) -> str:
            s = (s or "").strip()
            s = re.sub(r"^\s*\d+(?:\.\d+)*\s*[–—-]\s*", "", s)
            s = re.sub(r"\s{2,}", " ", s)
            return s.strip().lower()

        # Liste des couples (chapter, section) pour le lot (pour éviter le dropdown chapitre)
        pairs = conn.execute(
            """
            SELECT DISTINCT chapter, section
            FROM dpgf_articles
            WHERE row_type='article'
              AND lot=?
              AND (is_hidden IS NULL OR is_hidden=0)
              AND chapter IS NOT NULL AND TRIM(chapter) != ''
              AND section IS NOT NULL AND TRIM(section) != ''
            ORDER BY chapter, section
            """,
            (lot,),
        ).fetchall()

        section_choices = []
        for r in pairs:
            ch = r["chapter"]
            sec = r["section"]
            section_choices.append({
                "chapter": ch,
                "section": sec,
                "label": f"{ch} ➔ {sec}",
            })

        return jsonify({
            "lot": lot,
            "devis": {"chapter": devis_ch, "section": devis_sec},
            "section_choices": section_choices,
        })
    finally:
        conn.close()


@app.route('/api/matching/line/<int:line_id>/create_article', methods=['POST'])
def matching_line_create_article(line_id):
    """Crée un article custom (DPGF) depuis une ligne devis, puis mappe la ligne."""
    from utils import calculate_weighted_price, get_effective_date

    data = request.get_json(force=True)
    designation = (data.get('designation') or '').strip()
    unit = (data.get('unit') or '').strip()
    pu_ht = data.get('pu_ht')
    chapter = (data.get('chapter') or '').strip()
    section = (data.get('section') or '').strip()

    if not designation:
        return jsonify({'error': 'La désignation est obligatoire.'}), 400
    if not unit:
        return jsonify({'error': 'L’unité est obligatoire.'}), 400
    if not chapter:
        return jsonify({'error': 'Le chapitre est requis pour créer un article.'}), 400

    try:
        pu_val = float(pu_ht) if pu_ht not in (None, '') else 0.0
    except Exception:
        return jsonify({'error': 'Le PU doit être un nombre positif.'}), 400
    if pu_val < 0:
        return jsonify({'error': 'Le PU doit être un nombre positif.'}), 400

    conn = models.get_db(line_id=line_id)
    try:
        line = conn.execute(
            "SELECT project_id, original_designation, unit, unit_price_ht, lot, context_path FROM devis_lines WHERE id=?",
            (line_id,),
        ).fetchone()
        if not line:
            return jsonify({'error': 'Ligne introuvable'}), 404

        lot = (line['lot'] or 'CFO').upper()

        # row_order : max existant dans le même chapitre/section + 1
        max_order = conn.execute(
            "SELECT COALESCE(MAX(row_order), 0) FROM dpgf_articles WHERE chapter=? AND section=?",
            (chapter, section),
        ).fetchone()[0]

        cur = conn.execute(
            """
            INSERT INTO dpgf_articles
              (designation, unit, chapter, section, row_order,
               row_type, ratio_type, is_custom, qty_ref, lot, pu_ht_ref)
            VALUES (?, ?, ?, ?, ?, 'article', 'UNITAIRE', 1, 0, ?, ?)
            """,
            (designation, unit, chapter, section, max_order + 1, lot, pu_val),
        )
        new_art_id = cur.lastrowid

        # ratio_overrides (optionnel, mais utile si le reste de l'app s'appuie dessus)
        if pu_val > 0:
            conn.execute(
                """
                INSERT INTO ratio_overrides (dpgf_article_id, pu_override, raison)
                VALUES (?, ?, 'Création depuis matching')
                ON CONFLICT(dpgf_article_id) DO UPDATE SET
                    pu_override = excluded.pu_override,
                    created_at  = CURRENT_TIMESTAMP
                """,
                (new_art_id, pu_val),
            )

        # Mappe la ligne devis
        conn.execute(
            """
            UPDATE devis_lines
            SET dpgf_article_id = ?, mapping_status = 'manual',
                weighted_price_override = NULL
            WHERE id = ?
            """,
            (new_art_id, line_id),
        )

        conn.commit()

        # Recalcul prix pondéré (même forme que /select)
        line2 = conn.execute("SELECT * FROM devis_lines WHERE id=?", (line_id,)).fetchone()
        article = conn.execute("SELECT * FROM dpgf_articles WHERE id=?", (new_art_id,)).fetchone()
        proj = conn.execute(
            "SELECT devis_date FROM projects WHERE id=?", (dict(line2)['project_id'],)
        ).fetchone()

        result = {
            'status': 'ok',
            'weighted_price': None,
            'wp_tooltip': None,
            'ecart_pct': None,
            'base_designation': None,
            'base_pu': None,
            'article_id': new_art_id,
        }
        if line2 and article and proj:
            eff_date = get_effective_date(conn, new_art_id)
            wp = calculate_weighted_price(
                base_price=article['pu_ht_ref'],
                base_date=eff_date,
                devis_price=line2['unit_price_ht'],
                devis_date=proj['devis_date'],
            )
            result['weighted_price'] = wp.get('weighted_price')
            result['wp_tooltip'] = wp
            result['base_designation'] = article['designation']
            result['base_pu'] = article['pu_ht_ref']
            result['base_chapter'] = article['chapter'] or None
            result['base_section'] = article['section'] or None
            if wp.get('weighted_price') and line2['unit_price_ht']:
                result['ecart_pct'] = round(
                    (wp['weighted_price'] - line2['unit_price_ht']) / line2['unit_price_ht'] * 100, 1
                )
        result['wp_manual'] = False

        return jsonify(result)
    except Exception as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 500
    finally:
        conn.close()


@app.route('/api/matching/line/<int:line_id>/select', methods=['POST'])
def matching_line_select(line_id):
    from utils import calculate_weighted_price, get_effective_date

    data            = request.get_json(force=True)
    dpgf_article_id = data.get('dpgf_article_id')
    memorize        = data.get('memorize_synonym', False)
    cleaned_term    = (data.get('cleaned_term') or '').strip()

    if not dpgf_article_id:
        return jsonify({'error': 'dpgf_article_id requis'}), 400

    conn = models.get_db(line_id=line_id)
    try:
        conn.execute("""
            UPDATE devis_lines
            SET dpgf_article_id = ?, mapping_status = 'manual',
                weighted_price_override = NULL
            WHERE id = ?
        """, (dpgf_article_id, line_id))

        if memorize and cleaned_term:
            article = conn.execute(
                "SELECT designation FROM dpgf_articles WHERE id=?", (dpgf_article_id,)
            ).fetchone()
            if article:
                conn.execute(
                    "INSERT OR REPLACE INTO synonyms (original_term, mapped_term) VALUES (?,?)",
                    (cleaned_term, article['designation'])
                )

        conn.commit()

        # Recalcul prix pondéré
        line    = conn.execute("SELECT * FROM devis_lines WHERE id=?", (line_id,)).fetchone()
        article = conn.execute("SELECT * FROM dpgf_articles WHERE id=?", (dpgf_article_id,)).fetchone()
        proj    = conn.execute(
            "SELECT devis_date FROM projects WHERE id=?", (dict(line)['project_id'],)
        ).fetchone()

        result = {'status': 'ok', 'weighted_price': None, 'wp_tooltip': None, 'ecart_pct': None,
                  'base_designation': None, 'base_pu': None}
        if line and article and proj:
            eff_date = get_effective_date(conn, dpgf_article_id)
            wp = calculate_weighted_price(
                base_price=article['pu_ht_ref'],
                base_date=eff_date,
                devis_price=line['unit_price_ht'],
                devis_date=proj['devis_date'],
            )
            result['weighted_price']   = wp.get('weighted_price')
            result['wp_tooltip']       = wp
            result['base_designation'] = article['designation']
            result['base_pu']          = article['pu_ht_ref']
            result['base_chapter']     = article['chapter'] or None
            result['base_section']     = article['section'] or None
            if wp.get('weighted_price') and line['unit_price_ht']:
                result['ecart_pct'] = round(
                    (wp['weighted_price'] - line['unit_price_ht']) / line['unit_price_ht'] * 100, 1
                )
        result['wp_manual'] = False

        return jsonify(result)
    except Exception as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 500
    finally:
        conn.close()


@app.route('/api/matching/line/<int:line_id>/weighted_price', methods=['POST'])
def matching_line_weighted_price(line_id):
    """Sauvegarde ou efface le PU calculé (pondéré) manuel pour une ligne devis."""
    data = request.get_json(force=True) or {}
    raw_val = data.get('weighted_price')

    conn = models.get_db(line_id=line_id)
    try:
        chk = conn.execute(
            "SELECT id, mapping_status, project_id FROM devis_lines WHERE id=?",
            (line_id,),
        ).fetchone()
        if not chk:
            return jsonify({'error': 'Ligne introuvable'}), 404
        if chk['mapping_status'] == 'excluded':
            return jsonify({'error': 'Ligne exclue — PU calculé non modifiable.'}), 400

        if raw_val is None or (isinstance(raw_val, str) and not str(raw_val).strip()):
            conn.execute(
                "UPDATE devis_lines SET weighted_price_override=NULL WHERE id=?",
                (line_id,),
            )
        else:
            try:
                v = float(raw_val)
            except (TypeError, ValueError):
                return jsonify({'error': 'PU calculé : nombre invalide.'}), 400
            if v < 0 or v != v:
                return jsonify({'error': 'PU calculé : nombre invalide.'}), 400
            conn.execute(
                "UPDATE devis_lines SET weighted_price_override=? WHERE id=?",
                (round(v, 4), line_id),
            )

        conn.commit()

        row = conn.execute(
            """
            SELECT dl.id, dl.project_id, dl.original_designation, dl.unit, dl.quantity,
                   dl.unit_price_ht, dl.total_ht, dl.mapping_status,
                   dl.mapping_score, dl.row_type, dl.context_path, dl.lot,
                   dl.dpgf_article_id, dl.weighted_price_override,
                   da.designation  AS base_designation,
                   da.pu_ht_ref    AS base_pu,
                   da.unit         AS base_unit,
                   da.last_updated AS base_last_updated,
                   da.chapter      AS base_chapter,
                   da.section      AS base_section
            FROM devis_lines dl
            LEFT JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
            WHERE dl.id=?
            """,
            (line_id,),
        ).fetchone()
        proj = conn.execute(
            "SELECT devis_date FROM projects WHERE id=?",
            (row['project_id'],),
        ).fetchone()
        r = dict(row)
        _apply_matching_line_weighted_pricing(r, conn, proj['devis_date'] if proj else None)

        return jsonify(
            {
                'status': 'ok',
                'weighted_price': r.get('weighted_price'),
                'wp_tooltip': r.get('wp_tooltip'),
                'ecart_pct': r.get('ecart_pct'),
                'wp_manual': bool(r.get('wp_manual')),
            }
        )
    except Exception as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 500
    finally:
        conn.close()


@app.route('/api/matching/line/<int:line_id>/exclude', methods=['POST'])
def matching_line_exclude(line_id):
    conn = models.get_db(line_id=line_id)
    try:
        line = conn.execute(
            "SELECT mapping_status FROM devis_lines WHERE id=?", (line_id,)
        ).fetchone()
        if not line:
            return jsonify({'error': 'Ligne introuvable'}), 404
        prev       = line['mapping_status']
        new_status = 'auto' if prev == 'excluded' else 'excluded'
        conn.execute("UPDATE devis_lines SET mapping_status=? WHERE id=?", (new_status, line_id))
        conn.commit()
        return jsonify({'status': 'ok', 'new_status': new_status})
    finally:
        conn.close()


@app.route('/api/matching/synonym', methods=['POST'])
def matching_add_synonym():
    data     = request.get_json(force=True)
    original = (data.get('original_term') or '').strip()
    mapped   = (data.get('mapped_term')   or '').strip()
    if not original or not mapped:
        return jsonify({'error': 'original_term et mapped_term requis'}), 400
    prof = data.get('profil') or _request_profil()
    conn = models.get_db(normalize_profile(prof) if prof else db_profiles.DEFAULT_PROFILE)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO synonyms (original_term, mapped_term) VALUES (?,?)",
            (original, mapped)
        )
        conn.commit()
        return jsonify({'status': 'ok'})
    finally:
        conn.close()


@app.route('/api/matching/<int:project_id>/validate', methods=['POST'])
def matching_validate(project_id):
    conn = models.get_db(project_id=project_id)
    try:
        conn.execute("UPDATE projects SET import_ok=1 WHERE id=?", (project_id,))
        conn.commit()
        return jsonify({'status': 'ok'})
    finally:
        conn.close()


# ─── Watchdog ─────────────────────────────────────────────────────────────────
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
    port = int(os.environ.get('PORT', '8080'))
    print("=" * 60)
    print(" Estimation Élec — Application Web Locale")
    print(f" http://localhost:{port}")
    print("=" * 60, flush=True)

    # Démarre le watchdog de fermeture auto (heartbeat navigateur)
    threading.Thread(target=_watchdog_loop, daemon=True).start()

    app.run(debug=True, host='0.0.0.0', port=port, use_reloader=False)
