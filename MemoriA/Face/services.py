# Appels aux services IA du module Face
#   - Légende automatique : BLIP (Hugging Face ou local), traduite en français (opus-mt-en-fr, local)
#   - Objets détectés : DETR (Hugging Face ou local)
#   - Visages : détection YuNet, reconnaissance SFace (OpenCV, local) et regroupement DBSCAN (scikit-learn)
import io
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from PIL import Image

from Account.services import AIServiceError, log_ai_call, run_with_fallback

from .models import FaceTag, Person

MODELS_DIR = Path(settings.BASE_DIR) / 'Face' / 'ai_models'
YUNET_FILE = 'face_detection_yunet_2023mar.onnx'
SFACE_FILE = 'face_recognition_sface_2021dec.onnx'

DETECTION_THRESHOLD = 0.8     # score minimal d'un visage (valeur recommandée par OpenCV)
MATCH_THRESHOLD = 0.363       # similarité cosinus minimale pour dire « c'est la même personne » (OpenCV)
OBJECT_THRESHOLD = 0.7        # score minimal d'un objet détecté
MAX_SIDE = 1280               # les grandes photos sont réduites avant la détection (plus rapide)

# Noms français des 80 objets reconnus par DETR (jeu de données COCO)
OBJECTS_FR = {
    'person': 'personne', 'bicycle': 'vélo', 'car': 'voiture', 'motorcycle': 'moto', 'airplane': 'avion',
    'bus': 'bus', 'train': 'train', 'truck': 'camion', 'boat': 'bateau', 'traffic light': 'feu de circulation',
    'fire hydrant': "bouche d'incendie", 'stop sign': 'panneau stop', 'parking meter': 'parcmètre', 'bench': 'banc',
    'bird': 'oiseau', 'cat': 'chat', 'dog': 'chien', 'horse': 'cheval', 'sheep': 'mouton', 'cow': 'vache',
    'elephant': 'éléphant', 'bear': 'ours', 'zebra': 'zèbre', 'giraffe': 'girafe', 'backpack': 'sac à dos',
    'umbrella': 'parapluie', 'handbag': 'sac à main', 'tie': 'cravate', 'suitcase': 'valise', 'frisbee': 'frisbee',
    'skis': 'skis', 'snowboard': 'snowboard', 'sports ball': 'ballon', 'kite': 'cerf-volant', 'baseball bat': 'batte',
    'baseball glove': 'gant de baseball', 'skateboard': 'skateboard', 'surfboard': 'planche de surf',
    'tennis racket': 'raquette', 'bottle': 'bouteille', 'wine glass': 'verre à vin', 'cup': 'tasse',
    'fork': 'fourchette', 'knife': 'couteau', 'spoon': 'cuillère', 'bowl': 'bol', 'banana': 'banane',
    'apple': 'pomme', 'sandwich': 'sandwich', 'orange': 'orange', 'broccoli': 'brocoli', 'carrot': 'carotte',
    'hot dog': 'hot-dog', 'pizza': 'pizza', 'donut': 'beignet', 'cake': 'gâteau', 'chair': 'chaise',
    'couch': 'canapé', 'potted plant': 'plante', 'bed': 'lit', 'dining table': 'table', 'toilet': 'toilettes',
    'tv': 'télévision', 'laptop': 'ordinateur portable', 'mouse': 'souris', 'remote': 'télécommande',
    'keyboard': 'clavier', 'cell phone': 'téléphone', 'microwave': 'micro-ondes', 'oven': 'four',
    'toaster': 'grille-pain', 'sink': 'évier', 'refrigerator': 'réfrigérateur', 'book': 'livre', 'clock': 'horloge',
    'vase': 'vase', 'scissors': 'ciseaux', 'teddy bear': 'ours en peluche', 'hair drier': 'sèche-cheveux',
    'toothbrush': 'brosse à dents',
}


# ---------- Image ----------

def image_bytes(photo, max_side=1024):
    """Photo réduite en JPEG pour l'envoyer aux modèles (moins de données, même résultat)."""
    with photo.image.open('rb') as file:
        picture = Image.open(file).convert('RGB')
    picture.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    picture.save(buffer, format='JPEG', quality=90)
    return buffer.getvalue()


def read_cv_image(photo):
    """Photo en tableau OpenCV (BGR), à sa taille d'origine pour que les cadres correspondent."""
    import cv2
    import numpy as np
    with photo.image.open('rb') as file:
        data = np.frombuffer(file.read(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise AIServiceError("La photo est illisible.")
    return image


# ---------- Clients et modèles (chargés une seule fois) ----------

@lru_cache(maxsize=1)
def get_hf_client():
    from huggingface_hub import InferenceClient
    if not settings.HF_TOKEN:
        raise AIServiceError("La clé HF_TOKEN est absente du fichier .env.")
    return InferenceClient(token=settings.HF_TOKEN, timeout=120)


@lru_cache(maxsize=1)
def get_local_captioner():
    # BLIP appelé directement (processeur + modèle) : fonctionne quelle que soit la version de transformers
    from transformers import BlipForConditionalGeneration, BlipProcessor
    processor = BlipProcessor.from_pretrained(settings.CAPTION_MODEL)
    model = BlipForConditionalGeneration.from_pretrained(settings.CAPTION_MODEL)
    return processor, model.eval()


@lru_cache(maxsize=1)
def get_local_object_detector():
    from transformers import pipeline
    return pipeline('object-detection', model=settings.OBJECT_MODEL, device='cpu')


@lru_cache(maxsize=1)
def get_translator():
    # Modèle Marian appelé directement (le pipeline « translation » n'existe plus dans transformers 5)
    from transformers import MarianMTModel, MarianTokenizer
    tokenizer = MarianTokenizer.from_pretrained(settings.TRANSLATION_MODEL)
    return tokenizer, MarianMTModel.from_pretrained(settings.TRANSLATION_MODEL).eval()


def model_path(name):
    path = MODELS_DIR / name
    if not path.exists():
        raise AIServiceError("Les modèles de visages sont absents : lancez python manage.py download_face_models.")
    return str(path)


@lru_cache(maxsize=1)
def get_face_detector():
    import cv2
    return cv2.FaceDetectorYN.create(model_path(YUNET_FILE), '', (320, 320), DETECTION_THRESHOLD)


@lru_cache(maxsize=1)
def get_face_recognizer():
    import cv2
    return cv2.FaceRecognizerSF.create(model_path(SFACE_FILE), '')


# ---------- Légende ----------

def call_hf_caption(data):
    try:
        return get_hf_client().image_to_text(data, model=settings.CAPTION_MODEL).generated_text
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service de légende ne répond pas. Réessayez dans un instant.") from error


def call_local_caption(data):
    try:
        import torch
        processor, model = get_local_captioner()
        inputs = processor(images=Image.open(io.BytesIO(data)).convert('RGB'), return_tensors='pt')
        with torch.no_grad():
            output = model.generate(**inputs, max_new_tokens=30)
        return processor.decode(output[0], skip_special_tokens=True)
    except Exception as error:
        raise AIServiceError("Le modèle de légende local n'a pas pu décrire la photo.") from error


def translate_to_french(text):
    """Traduction anglais -> français (modèle local). En cas d'échec, on garde l'anglais."""
    try:
        import torch
        tokenizer, model = get_translator()
        with torch.no_grad():
            output = model.generate(**tokenizer([text], return_tensors='pt'), max_new_tokens=60)
        return tokenizer.decode(output[0], skip_special_tokens=True)
    except Exception:
        return text


def caption_photo(photo, user):
    """Légende en français, prête à servir de texte alternatif."""
    english = run_with_fallback(settings.FACE_BACKEND, user, 'Légende de la photo',
                                ('BLIP', call_hf_caption), ('BLIP (local)', call_local_caption), image_bytes(photo))
    english = (english or '').strip()
    if not english:
        raise AIServiceError("Aucune légende n'a été proposée pour cette photo.")
    french = log_ai_call(user, 'Traduction de la légende', 'Traduction (local)', translate_to_french, english)
    return french[:1].upper() + french[1:]


# ---------- Objets ----------

def call_hf_objects(data):
    try:
        results = get_hf_client().object_detection(data, model=settings.OBJECT_MODEL, threshold=OBJECT_THRESHOLD)
        return [{'label': r.label, 'score': r.score} for r in results]
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service de détection d'objets ne répond pas. Réessayez dans un instant.") from error


def call_local_objects(data):
    try:
        results = get_local_object_detector()(Image.open(io.BytesIO(data)), threshold=OBJECT_THRESHOLD)
        return [{'label': r['label'], 'score': r['score']} for r in results]
    except Exception as error:
        raise AIServiceError("Le modèle de détection d'objets local n'a pas pu analyser la photo.") from error


def objects_in_french(results):
    """Noms français uniques, du plus sûr au moins sûr."""
    names = []
    for result in sorted(results, key=lambda r: r['score'], reverse=True):
        if result['score'] < OBJECT_THRESHOLD:
            continue
        name = OBJECTS_FR.get(result['label'], result['label'])
        if name not in names:
            names.append(name)
    return names


def detect_objects(photo, user):
    results = run_with_fallback(settings.FACE_BACKEND, user, 'Détection des objets',
                                ('DETR', call_hf_objects), ('DETR (local)', call_local_objects), image_bytes(photo))
    return objects_in_french(results)


# ---------- Visages : détection (YuNet) ----------

def detect_faces(image):
    """Visages d'une image OpenCV : [{x, y, width, height, score, row}] en pixels de l'image d'origine.
    row = ligne brute de YuNet (cadre + 5 points du visage), utile pour aligner le visage avant SFace."""
    import cv2
    height, width = image.shape[:2]
    scale = min(1.0, MAX_SIDE / max(width, height))
    small = image if scale == 1.0 else cv2.resize(image, (int(width * scale), int(height * scale)))
    detector = get_face_detector()
    detector.setInputSize((small.shape[1], small.shape[0]))
    _, faces = detector.detect(small)
    results = []
    for row in (faces if faces is not None else []):
        row = row.copy()
        row[:14] = row[:14] / scale                     # coordonnées ramenées à la taille d'origine
        x, y = max(0, int(row[0])), max(0, int(row[1]))
        w, h = min(int(row[2]), width - x), min(int(row[3]), height - y)
        if w > 0 and h > 0:
            results.append({'x': x, 'y': y, 'width': w, 'height': h, 'score': float(row[14]), 'row': row})
    return results


def overlap(a, b):
    """Recouvrement de deux cadres (IoU) : 0 = séparés, 1 = identiques."""
    left, top = max(a['x'], b['x']), max(a['y'], b['y'])
    right = min(a['x'] + a['width'], b['x'] + b['width'])
    bottom = min(a['y'] + a['height'], b['y'] + b['height'])
    inter = max(0, right - left) * max(0, bottom - top)
    union = a['width'] * a['height'] + b['width'] * b['height'] - inter
    return inter / union if union else 0


def tag_box(tag):
    return {'x': tag.x, 'y': tag.y, 'width': tag.width, 'height': tag.height}


# ---------- Visages : empreinte et reconnaissance (SFace) ----------

def face_feature(image, row):
    """Empreinte du visage (128 nombres), normalisée pour comparer par similarité cosinus."""
    import numpy as np
    recognizer = get_face_recognizer()
    feature = recognizer.feature(recognizer.alignCrop(image, row)).flatten().astype(float)
    return feature / (np.linalg.norm(feature) or 1)


def cosine(a, b):
    import numpy as np
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1))


def best_match(feature, persons):
    """Personne la plus ressemblante si la similarité dépasse le seuil, sinon (None, meilleure similarité)."""
    best, best_score = None, -1.0
    for person in persons:
        if person.face_encoding:
            score = cosine(feature, person.face_encoding)
            if score > best_score:
                best, best_score = person, score
    return (best, best_score) if best_score >= MATCH_THRESHOLD else (None, best_score)


def feature_for_tag(tag, image, detections):
    """Empreinte d'un visage enregistré : on retrouve sa détection (mêmes points du visage) par recouvrement."""
    import cv2
    import numpy as np
    match = max(detections, key=lambda d: overlap(d, tag_box(tag)), default=None)
    if match is not None and overlap(match, tag_box(tag)) > 0.3:
        return face_feature(image, match['row'])
    # Cadre tracé à la main sans détection : visage simplement recadré (un peu moins précis)
    crop = image[tag.y:tag.y + tag.height, tag.x:tag.x + tag.width]
    if crop.size == 0:
        return None
    feature = get_face_recognizer().feature(cv2.resize(crop, (112, 112))).flatten().astype(float)
    return feature / (np.linalg.norm(feature) or 1)


def analyze_faces(photo, user):
    """Détecte les visages, propose « C'est X ? » et crée les FaceTag. Renvoie la liste des visages créés."""
    image = read_cv_image(photo)
    detections = log_ai_call(user, 'Détection des visages', 'YuNet', detect_faces, image)

    # Nouvelle analyse : on retire les anciennes propositions de l'IA non confirmées
    photo.face_tags.filter(is_confirmed=False, confidence__lt=1).delete()
    kept = [tag_box(tag) for tag in photo.face_tags.all()]
    new_faces = [d for d in detections if all(overlap(d, box) <= 0.5 for box in kept)]

    persons = list(Person.objects.filter(user=user, face_encoding__isnull=False))

    def recognize():
        return [best_match(face_feature(image, face['row']), persons)[0] for face in new_faces]

    matches = log_ai_call(user, 'Reconnaissance des visages', 'SFace', recognize) if persons and new_faces \
        else [None] * len(new_faces)

    created = [FaceTag.objects.create(photo=photo, person=person, x=face['x'], y=face['y'], width=face['width'],
                                      height=face['height'], confidence=round(face['score'], 2), is_confirmed=False)
               for face, person in zip(new_faces, matches)]
    photo.faces_analyzed = True
    photo.save(update_fields=['faces_analyzed'])
    return created


def update_person_encoding(person, user):
    """Empreinte de référence d'une personne = moyenne des empreintes de ses visages confirmés."""
    import numpy as np
    features = []
    tags = FaceTag.objects.filter(person=person, is_confirmed=True).select_related('photo')
    for photo_tags in group_by_photo(tags).values():
        try:
            image = read_cv_image(photo_tags[0].photo)
        except (AIServiceError, OSError, ValueError):
            continue
        detections = detect_faces(image)
        features += [f for f in (feature_for_tag(tag, image, detections) for tag in photo_tags) if f is not None]
    if features:
        mean = np.mean(features, axis=0)
        person.face_encoding = [round(float(v), 6) for v in mean / (np.linalg.norm(mean) or 1)]
    else:
        person.face_encoding = None
    person.save(update_fields=['face_encoding'])
    return len(features)


def group_by_photo(tags):
    groups = {}
    for tag in tags:
        groups.setdefault(tag.photo_id, []).append(tag)
    return groups


# ---------- Visages inconnus : regroupement (DBSCAN) ----------

def cluster_faces(features):
    """DBSCAN en distance cosinus : deux visages sont voisins si leur similarité dépasse le seuil de SFace.
    Renvoie un numéro de groupe par visage (-1 = visage isolé)."""
    from sklearn.cluster import DBSCAN
    if len(features) < 2:
        return [-1] * len(features)
    return DBSCAN(eps=1 - MATCH_THRESHOLD, min_samples=2, metric='cosine').fit_predict(features).tolist()


def group_unknown_faces(user):
    """Groupes de visages sans nom qui semblent être la même personne : [[id, id, ...], ...]."""
    unknown = FaceTag.objects.filter(photo__entry__user=user, person__isnull=True).select_related('photo')
    ids, features = [], []
    for photo_tags in group_by_photo(unknown).values():
        try:
            image = read_cv_image(photo_tags[0].photo)
        except (AIServiceError, OSError, ValueError):
            continue
        detections = log_ai_call(user, 'Détection des visages', 'YuNet', detect_faces, image)
        for tag in photo_tags:
            feature = feature_for_tag(tag, image, detections)
            if feature is not None:
                ids.append(tag.pk)
                features.append(feature)
    labels = log_ai_call(user, 'Regroupement des visages', 'DBSCAN', cluster_faces, features)
    groups = {}
    for tag_id, label in zip(ids, labels):
        if label != -1:
            groups.setdefault(label, []).append(tag_id)
    return sorted(groups.values(), key=len, reverse=True)
