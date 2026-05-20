"""Router com endpoints do sistema de classificação manual por usuários.

Rotas separadas do sistema de extração/conferência.
Protegidas por autenticação JWT.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, inspect, text as sql_text
from typing import Optional, List
from math import ceil
from datetime import datetime, timedelta, timezone
from loguru import logger
import re

from jose import JWTError, jwt
import bcrypt

from ..config import settings
from ..database import get_db
from ..database.models import QuestaoModel, HabilidadeModel, DisciplinaModel
from ..database.pg_models import QuestaoAssuntoModel
from ..database.pg_modulo_models import HabilidadeModuloModel
from ..database.pg_usuario_models import UsuarioModel, ClassificacaoUsuarioModel
from ..database.pg_pular_models import QuestaoPuladaModel
from ..database.pg_usuario_models import (
    QuestaoSuperprofessorModel,
    AlternativaSuperprofessorModel,
)
from ..services.enunciado_cleaner import tratar_enunciado
from .classificacao_schemas import (
    CadastroRequest,
    LoginRequest,
    TokenResponse,
    UsuarioSchema,
    HabilidadeModuloSchema,
    ModulosResponse,
    AssuntoVinculadoSchema,
    ModuloComAssuntosSchema,
    ModulosAssuntosResponse,
    HabilidadeFiltroSchema,
    HabilidadesFiltroResponse,
    AlternativaClassifSchema,
    ClassificacaoManualResumoSchema,
    QuestaoClassifResponse,
    SalvarClassificacaoRequest,
    SalvarClassificacaoResponse,
    PularQuestaoRequest,
    PularQuestaoResponse,
    ClassificacaoStatsResponse,
    ClassificacaoHistoricoSchema,
    HistoricoListResponse,
    QuestaoSuperprofessorResponse,
    AlternativaSuperprofessorSchema,
    SuperprofessorStatsResponse,
    SalvarSuperprofessorRequest,
    PularSuperprofessorRequest,
)

import time

# ========================
# CACHE EM MEMÓRIA (TTL)
# ========================
_api_cache = {}


def get_from_cache(key: str, ttl: int = 300):
    if key in _api_cache:
        val, ts = _api_cache[key]
        if time.time() - ts < ttl:
            return val
    return None


def set_to_cache(key: str, val):
    _api_cache[key] = (val, time.time())


def _pick_first_column(available: set[str], candidates: list[str]) -> Optional[str]:
    """Retorna a primeira coluna existente na lista de candidatos."""
    for col in candidates:
        if col in available:
            return col
    return None


def _sql_ident(name: str) -> str:
    """Valida e escapa identificadores SQL simples (tabelas/colunas)."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise HTTPException(
            status_code=500, detail=f"Nome de coluna inválido detectado: {name}"
        )
    return f"`{name}`"


def _normalize_text(value: Optional[str]) -> str:
    return " ".join((value or "").strip().lower().split())


def _normalize_disc_modu_id(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized.endswith(".0"):
        normalized = normalized[:-2]
    return normalized


def _resolver_habilidade_mysql_ids(
    trieduc_habilidade_id: int,
    pg_db: Session,
    db: Session,
) -> list[int]:
    """Resolve um habilidade_id TRIEDUC para os IDs correspondentes no MySQL.

    Os dois sistemas (PostgreSQL/TRIEDUC e MySQL) usam IDs independentes para a
    mesma habilidade. O /habilidades usa mapeamento via descrição para contar
    corretamente; este helper aplica a mesma lógica nos endpoints /proxima.
    """
    hab_desc_row = (
        pg_db.query(HabilidadeModuloModel.habilidade_descricao)
        .filter(HabilidadeModuloModel.habilidade_id == trieduc_habilidade_id)
        .first()
    )
    if not hab_desc_row or not hab_desc_row[0]:
        return [trieduc_habilidade_id]

    mysql_ids = [
        r[0]
        for r in db.query(HabilidadeModel.id)
        .filter(func.lower(HabilidadeModel.descricao) == hab_desc_row[0].lower())
        .all()
    ]
    return mysql_ids if mysql_ids else [trieduc_habilidade_id]


# ========================
# CONFIG
# ========================
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_expire_minutes

# bcrypt 5.x — usar diretamente (passlib não compatível)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/classificacao/login")

router = APIRouter(prefix="/classificacao", tags=["Classificação Manual"])

# Disciplinas válidas para cadastro (incluindo Áreas)
DISCIPLINAS_VALIDAS = [
    "Artes",
    "Biologia",
    "Ciências",
    "Educação Física",
    "Espanhol",
    "Filosofia",
    "Física",
    "Geografia",
    "História",
    "Língua Inglesa",
    "Língua Portuguesa",
    "Literatura",
    "Matemática",
    "Natureza e Sociedade",
    "Química",
    "Redação",
    "Sociologia",
    # Áreas
    "Humanas",
    "Linguagens",
    "Natureza",
]

# Mapeamento para o MySQL (onde os nomes podem ser diferentes do Postgres/Planilha)
# Pares (disc_modu_id, assu_id) mapeados para busca SP — embutido para não depender de arquivo externo
_MAPEAMENTO_SP_PAIRS: frozenset = frozenset({
    (1000,10000), (1000,10094), (1000,10096), (1001,10001), (1001,10010), (1001,10011), (1001,10012), (1001,10013),
    (1001,10014), (1001,10015), (1001,10016), (1002,10002), (1002,10003), (1002,10004), (1002,10021), (1002,10049),
    (1002,10058), (1002,10061), (1002,10070), (1003,10005), (1003,10006), (1003,10007), (1003,10051), (1003,10077),
    (1004,10017), (1004,10023), (1004,10024), (1004,10025), (1004,10055), (1004,10056), (1005,10018), (1005,10026),
    (1005,10048), (1005,10052), (1005,10053), (1005,10057), (1005,10060), (1005,10068), (1005,10069), (1006,10065),
    (1007,10027), (1007,10028), (1007,10029), (1007,10030), (1007,10031), (1008,10033), (1008,10034), (1008,10035),
    (1008,10036), (1008,10037), (1008,10038), (1008,10039), (1008,10040), (1008,10064), (1009,10041), (1009,10042),
    (1009,10043), (1009,10044), (1009,10045), (1009,10046), (1009,10047), (1009,10050), (1010,10062), (1010,10072),
    (1010,10073), (1010,10074), (1011,10085), (1011,10086), (1011,10087), (1011,10088), (1011,10098), (1012,10079),
    (1012,10080), (1012,10083), (1012,10091), (1012,10092), (1013,10211), (1016,10197), (1018,10205), (1021,10254),
    (1022,10198), (1022,10199), (1024,10124), (1024,10125), (1028,10129), (1028,10148), (1030,10163), (1032,10165),
    (1032,10206), (1032,10279), (1032,10281), (1033,10170), (1037,10192), (1038,10263), (1038,10264), (1039,10200),
    (1039,10201), (1039,10202), (1039,10203), (1039,10204), (1040,10209), (1041,10221), (1042,10214), (1042,10235),
    (1042,10237), (1042,10238), (1042,10239), (1042,10240), (1042,10241), (1042,10242), (1043,10215), (1043,10284),
    (1043,10285), (1044,10262), (1046,10232), (1046,10266), (1046,12056), (1047,10233), (1049,10249), (1050,10257),
    (1050,10258), (1050,10259), (1050,10260), (1050,10261), (1051,10256), (1051,10267), (1052,10268), (1052,10269),
    (1052,10276), (1053,10270), (1053,10282), (1054,10292), (1055,10531), (1055,10533), (1055,10534), (1055,10535),
    (1056,10298), (1056,10463), (1056,10464), (1056,10486), (1056,10495), (1057,10299), (1057,10300), (1057,10301),
    (1057,10508), (1057,10509), (1058,10303), (1058,10337), (1059,10304), (1059,10307), (1059,10308), (1059,10309),
    (1060,10311), (1060,10312), (1060,10363), (1060,10364), (1061,10317), (1061,10318), (1061,10319), (1061,10320),
    (1061,10321), (1062,10315), (1062,10316), (1062,10332), (1062,10333), (1062,10334), (1062,10336), (1063,10322),
    (1063,10323), (1063,10324), (1063,10325), (1063,10326), (1064,10327), (1064,10328), (1064,10329), (1064,10330),
    (1064,10331), (1065,10338), (1065,10339), (1065,10340), (1065,10341), (1066,10345), (1066,10346), (1066,10348),
    (1066,10351), (1067,10352), (1067,10353), (1067,10354), (1067,10355), (1067,10371), (1068,10357), (1068,10358),
    (1068,10359), (1068,10360), (1068,10361), (1068,10362), (1069,10365), (1069,10366), (1069,10367), (1069,10368),
    (1069,10369), (1069,10370), (1070,10372), (1070,10373), (1070,10374), (1070,10375), (1070,10376), (1070,10377),
    (1070,10378), (1071,10380), (1071,10381), (1071,10382), (1071,10383), (1071,10384), (1071,10385), (1071,10386),
    (1071,10387), (1071,10388), (1071,10389), (1072,10391), (1072,10470), (1073,10422), (1073,10423), (1073,10424),
    (1074,10397), (1074,10399), (1075,10403), (1075,10406), (1075,10407), (1075,10408), (1076,10411), (1076,10412),
    (1076,10413), (1076,10414), (1076,12058), (1077,10415), (1077,10416), (1077,10417), (1077,10418), (1077,10419),
    (1077,10420), (1078,10425), (1078,10426), (1078,10427), (1078,10428), (1078,10429), (1078,10430), (1079,10431),
    (1079,10432), (1079,10433), (1079,10434), (1079,10435), (1079,10436), (1080,10437), (1080,10438), (1080,10439),
    (1080,10441), (1081,10442), (1081,10443), (1081,10445), (1081,10456), (1082,10446), (1082,10447), (1082,10448),
    (1082,10449), (1083,10457), (1084,10462), (1084,10469), (1084,10471), (1084,10472), (1084,10473), (1085,10465),
    (1085,10466), (1085,10467), (1085,10468), (1086,10474), (1086,10475), (1086,10476), (1086,10477), (1086,10478),
    (1086,10479), (1086,10481), (1087,10483), (1087,10485), (1087,10487), (1087,10488), (1088,10484), (1088,10489),
    (1088,10490), (1088,10491), (1088,10492), (1088,10493), (1089,10529), (1089,10530), (1089,10536), (1089,10537),
    (1089,10538), (1090,10497), (1090,10498), (1090,10499), (1090,10500), (1090,10501), (1091,10502), (1091,10503),
    (1091,10504), (1091,10505), (1091,10506), (1092,10507), (1092,10527), (1092,10528), (1093,10510), (1093,10511),
    (1093,10512), (1093,10513), (1093,10514), (1094,10515), (1094,10516), (1094,10566), (1094,10567), (1094,10568),
    (1094,10569), (1095,10519), (1095,10520), (1095,10521), (1096,10539), (1096,10541), (1096,10544), (1098,10557),
    (1098,10558), (1098,10559), (1098,10560), (1098,10561), (1098,10562), (1098,10563), (1099,10565), (1099,10572),
    (1099,10573), (1099,10574), (1100,10575), (1100,10576), (1100,10583), (1101,10577), (1101,10579), (1101,10580),
    (1101,10581), (1102,10578), (1102,10600), (1102,10603), (1102,10604), (1104,10590), (1104,10599), (1105,10606),
    (1106,10597), (1106,10598), (1111,10616), (1111,10617), (1113,10621), (1113,10623), (1114,10624), (1114,10627),
    (1115,10628), (1115,10629), (1116,10630), (1117,10633), (1117,10634), (1117,10635), (1117,10636), (1118,10638),
    (1118,10639), (1119,10641), (1119,10642), (1120,10643), (1120,10644), (1120,10645), (1121,10646), (1121,10647),
    (1122,10648), (1122,10649), (1123,10650), (1123,10651), (1124,10652), (1125,10653), (1125,10654), (1125,10655),
    (1126,10656), (1127,10657), (1127,10658), (1127,10659), (1127,10660), (1127,10661), (1128,10662), (1128,10663),
    (1128,10664), (1129,10665), (1130,10666), (1130,10667), (1130,10668), (1131,10670), (1131,10671), (1131,10672),
    (1131,10673), (1131,10674), (1132,10675), (1132,10676), (1132,10678), (1132,10680), (1132,10681), (1132,10682),
    (1133,10683), (1133,10684), (1134,10685), (1134,10686), (1135,10687), (1135,10688), (1135,10689), (1136,10690),
    (1136,10691), (1137,10693), (1137,10694), (1137,10695), (1137,10696), (1137,10697), (1137,10698), (1137,10699),
    (1137,10700), (1137,10701), (1138,10702), (1138,10704), (1138,10705), (1139,10707), (1139,10709), (1139,10711),
    (1139,10712), (1139,10713), (1142,10720), (1142,10721), (1142,10722), (1142,10723), (1144,10726), (1145,10728),
    (1145,10729), (1145,10730), (1145,10731), (1145,10732), (1145,10733), (1145,10734), (1145,10735), (1145,10736),
    (1145,10737), (1145,10738), (1145,10739), (1145,10740), (1145,10741), (1146,10757), (1151,10770), (1153,10773),
    (1155,10780), (1160,10793), (1163,10801), (1163,10802), (1163,10803), (1164,10812), (1165,10813), (1166,10832),
    (1166,10833), (1168,10836), (1168,10926), (1169,10837), (1169,10934), (1169,10935), (1170,10839), (1171,10913),
    (1172,10846), (1172,10847), (1172,10848), (1172,10914), (1173,10850), (1174,10852), (1175,10854), (1176,10855),
    (1177,10858), (1177,10859), (1178,10861), (1179,10863), (1180,10864), (1180,10865), (1183,10874), (1184,10876),
    (1185,10881), (1192,10900), (1193,10903), (1193,10905), (1193,10906), (1193,10907), (1193,10908), (1194,10910),
    (1194,10911), (1194,10912), (1195,10915), (1195,12065), (1196,10916), (1196,10917), (1197,10918), (1197,10919),
    (1197,10920), (1197,10921), (1198,10922), (1199,10923), (1202,10932), (1202,10933), (1203,10936), (1203,10937),
    (1203,10938), (1206,10965), (1206,10967), (1208,10981), (1211,10997), (1211,12067), (1218,11049), (1219,11060),
    (1221,11071), (1222,11083), (1223,11093), (1224,11100), (1224,11102), (1224,11103), (1224,11104), (1224,11105),
    (1225,11108), (1225,11109), (1225,11110), (1227,11122), (1227,11123), (1228,11124), (1228,11125), (1228,11127),
    (1228,11128), (1230,11135), (1230,11138), (1230,11139), (1230,11140), (1230,11141), (1230,11142), (1230,11143),
    (1230,11144), (1231,11149), (1232,11154), (1232,11156), (1232,11157), (1233,11159), (1233,11160), (1233,11162),
    (1234,11165), (1234,11166), (1234,11167), (1234,11168), (1234,11169), (1234,11170), (1234,11171), (1235,11175),
    (1235,11176), (1235,11177), (1236,11178), (1236,11179), (1236,11180), (1236,11181), (1236,11182), (1236,11183),
    (1237,11187), (1237,11188), (1237,11189), (1238,11191), (1238,11196), (1238,11197), (1238,11198), (1239,11204),
    (1239,11205), (1241,11216), (1241,11218), (1241,11219), (1241,12066), (1242,11222), (1242,11223), (1242,11224),
    (1242,11225), (1242,11226), (1242,11227), (1243,11228), (1243,11229), (1243,11230), (1243,11231), (1243,11232),
    (1243,11233), (1244,11234), (1244,11235), (1244,11236), (1244,11237), (1244,11238), (1244,11239), (1244,11240),
    (1244,11241), (1244,11242), (1245,11244), (1245,11245), (1245,11246), (1245,11248), (1246,11249), (1246,11250),
    (1246,11251), (1246,11254), (1246,11256), (1247,11257), (1247,11258), (1247,11259), (1247,11260), (1248,11261),
    (1248,11262), (1248,11263), (1248,11265), (1248,11266), (1248,11267), (1248,11270), (1248,11273), (1250,11277),
    (1250,11278), (1250,11279), (1250,11280), (1250,11281), (1250,11282), (1250,11283), (1250,11284), (1250,11285),
    (1250,11287), (1250,11289), (1251,11290), (1251,11291), (1251,11292), (1252,11294), (1252,11297), (1252,11298),
    (1253,11299), (1253,11303), (1253,11304), (1254,11305), (1254,11306), (1254,11307), (1254,11308), (1255,11312),
    (1256,11318), (1256,11319), (1256,11320), (1256,11321), (1256,11322), (1256,11323), (1257,11329), (1257,11331),
    (1259,11332), (1259,11333), (1259,11335), (1259,11336), (1259,11337), (1259,11338), (1259,11340), (1259,11341),
    (1259,11342), (1259,11344), (1259,11345), (1261,11350), (1262,11354), (1262,11357), (1263,11365), (1266,11375),
    (1266,11377), (1266,11378), (1266,11379), (1266,11380), (1266,11381), (1266,11382), (1266,11383), (1268,11387),
    (1268,11388), (1268,11389), (1268,11390), (1268,11391), (1269,11397), (1270,11399), (1270,11400), (1270,11401),
    (1270,11402), (1270,11403), (1270,11404), (1272,11409), (1272,11414), (1273,11421), (1274,11426), (1274,11427),
    (1274,11428), (1274,11429), (1274,11432), (1274,11433), (1274,11434), (1274,11435), (1274,11436), (1274,11437),
    (1274,11438), (1274,11440), (1274,11441), (1274,11442), (1274,11443), (1274,11444), (1276,11448), (1276,11449),
    (1276,11451), (1276,11456), (1277,11457), (1277,11458), (1277,11461), (1278,11463), (1278,11465), (1278,11466),
    (1278,11467), (1278,11468), (1278,11469), (1278,11470), (1280,11473), (1280,11482), (1280,11483), (1281,11484),
    (1281,11485), (1281,11488), (1281,11489), (1281,11490), (1281,12052), (1282,11492), (1283,11494), (1283,11496),
    (1283,11497), (1284,11498), (1284,11499), (1285,11501), (1285,11503), (1285,11504), (1285,11505), (1286,11507),
    (1286,11508), (1286,11509), (1286,11510), (1286,11511), (1287,11512), (1287,11513), (1287,11514), (1288,11515),
    (1288,11516), (1288,11517), (1289,11518), (1289,11519), (1290,11520), (1290,11521), (1290,11522), (1290,12060),
    (1291,11523), (1291,11524), (1292,11529), (1293,11536), (1294,11541), (1296,11556), (1297,11561), (1297,11562),
    (1298,11571), (1301,11587), (1301,11591), (1302,11594), (1302,11597), (1302,11600), (1304,11611), (1304,11613),
    (1305,11614), (1305,11615), (1306,11620), (1306,11623), (1306,11624), (1306,11625), (1306,11626), (1307,11631),
    (1309,11636), (1309,11641), (1309,11643), (1310,11645), (1310,11650), (1310,11651), (1310,11652), (1310,11653),
    (1310,11654), (1312,11659), (1312,11660), (1312,11662), (1312,11663), (1313,11664), (1313,11667), (1313,11668),
    (1313,11669), (1313,11670), (1314,11673), (1314,11674), (1314,11675), (1314,11676), (1315,11679), (1315,11680),
    (1315,11681), (1315,11683), (1315,11685), (1316,11686), (1316,11687), (1316,11688), (1317,11689), (1317,11690),
    (1317,11691), (1317,11692), (1319,11698), (1319,11699), (1319,11701), (1320,11703), (1321,11706), (1322,11710),
    (1323,11711), (1323,11712), (1323,11714), (1323,11715), (1324,11716), (1324,11717), (1324,11718), (1324,11719),
    (1324,11721), (1324,11723), (1325,11725), (1326,11731), (1327,11736), (1327,11737), (1327,11738), (1327,11739),
    (1328,11740), (1328,11742), (1328,11743), (1329,11744), (1329,11745), (1329,11746), (1329,11747), (1330,11748),
    (1330,11749), (1330,11750), (1330,11751), (1330,11752), (1331,11753), (1331,11754), (1331,11755), (1331,11756),
    (1331,11757), (1332,11758), (1332,11759), (1332,11760), (1332,11761), (1334,11766), (1334,11769), (1334,11770),
    (1334,11771), (1334,11772), (1334,11773), (1334,11774), (1339,11803), (1339,11804), (1339,11808), (1343,11826),
    (1343,11827), (1346,11844), (1346,11846), (1346,11847), (1348,11859), (1350,11873), (1351,11874), (1351,11875),
    (1351,11876), (1351,11877), (1351,11878), (1351,11879), (1351,11880), (1351,11881), (1351,11882), (1352,11888),
    (1353,11898), (1355,11905), (1356,11908), (1356,11909), (1356,11910), (1356,11913), (1356,11914), (1356,11915),
    (1357,11918), (1357,11920), (1357,11921), (1358,11925), (1358,11927), (1358,11928), (1359,11933), (1359,11934),
    (1359,11935), (1359,11936), (1359,11937), (1360,11938), (1360,11939), (1360,11940), (1360,11941), (1361,11946),
    (1363,11957), (1363,11958), (1363,11959), (1364,11960), (1365,11963), (1365,11964), (1365,11965), (1365,11966),
    (1365,11967), (1365,11968), (1366,11969), (1366,11970), (1366,11971), (1367,11972), (1367,11974), (1368,11976),
    (1368,11977), (1368,11978), (1368,11979), (1369,11981), (1369,11982), (1369,11983), (1369,11984), (1371,11991),
    (1371,11992), (1371,11993), (1371,11994), (1371,11995), (1371,11996), (1371,12055), (1372,12001), (1373,12008),
    (1374,12012), (1374,12013), (1374,12014), (1374,12015), (1374,12016), (1511,12051), (1512,12059), (1513,12061),
    (1514,12063), (1515,10818), (1515,10819), (1515,10820), (1515,10822), (1515,10823),
})

MAP_DISCIPLINAS_MYSQL = {
    "Artes": "Artes",
    "Língua Inglesa": "Língua Inglesa",
    "Língua Portuguesa": "Língua Portuguesa",
    "Literatura": None,  # Não existe no MySQL
    "Redação": None,  # Não existe no MySQL
}

# Mapeamento de áreas para filtro
AREAS_DISCIPLINAS = {
    "Humanas": ["Filosofia", "Geografia", "História", "Sociologia"],
    "Linguagens": [
        "Artes",
        "Educação Física",
        "Espanhol",
        "Língua Inglesa",
        "Língua Portuguesa",
        "Literatura",
        "Redação",
    ],
    "Matemática": ["Matemática"],
    "Natureza": ["Biologia", "Ciências", "Física", "Natureza e Sociedade", "Química"],
}


# ========================
# HELPERS
# ========================


def criar_token(data: dict) -> str:
    """Cria um token JWT."""
    to_encode = data.copy()
    # JWT spec requires 'sub' to be a string
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verificar_senha(senha_plain: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(senha_plain.encode("utf-8"), senha_hash.encode("utf-8"))


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def get_usuario_atual(
    token: str = Depends(oauth2_scheme),
    pg_db: Session = Depends(get_db),
) -> UsuarioModel:
    """Dependency: extrai e valida o usuário do token JWT."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub_value = payload.get("sub")
        if sub_value is None:
            raise credentials_exception
        usuario_id = int(sub_value)
    except (JWTError, ValueError):
        raise credentials_exception

    usuario = pg_db.query(UsuarioModel).filter(UsuarioModel.id == usuario_id).first()
    if usuario is None or not usuario.ativo:
        raise credentials_exception
    return usuario


# ========================
# AUTENTICAÇÃO
# ========================


@router.post(
    "/cadastro",
    response_model=TokenResponse,
    summary="📝 Cadastrar novo usuário",
    status_code=status.HTTP_201_CREATED,
)
async def cadastrar_usuario(
    request: CadastroRequest,
    pg_db: Session = Depends(get_db),
):
    """
    Cadastra um novo usuário para classificação manual.
    O campo `disciplina` deve ser uma das disciplinas válidas do sistema.
    """
    # Validar disciplina
    if request.disciplina not in DISCIPLINAS_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Disciplina inválida. Opções: {', '.join(DISCIPLINAS_VALIDAS)}",
        )

    # Verificar email duplicado
    existente = (
        pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    )
    if existente:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    # Criar usuário
    usuario = UsuarioModel(
        nome=request.nome,
        email=request.email,
        senha_hash=hash_senha(request.senha),
        disciplina=request.disciplina,
    )
    pg_db.add(usuario)
    pg_db.commit()
    pg_db.refresh(usuario)

    # Gerar token
    token = criar_token({"sub": usuario.id})
    logger.info(f"Novo usuário cadastrado: {usuario.nome} ({usuario.disciplina})")

    return TokenResponse(
        access_token=token,
        usuario=UsuarioSchema.model_validate(usuario),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="🔑 Login",
)
async def login(
    request: LoginRequest,
    pg_db: Session = Depends(get_db),
):
    """Autentica o usuário e retorna um token JWT."""
    usuario = (
        pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    )
    if not usuario or not verificar_senha(request.senha, usuario.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )
    if not usuario.ativo:
        raise HTTPException(status_code=403, detail="Usuário desativado")

    token = criar_token({"sub": usuario.id})
    logger.info(f"Login: {usuario.nome}")

    return TokenResponse(
        access_token=token,
        usuario=UsuarioSchema.model_validate(usuario),
    )


@router.get(
    "/me",
    response_model=UsuarioSchema,
    summary="👤 Dados do usuário atual",
)
async def dados_usuario(usuario: UsuarioModel = Depends(get_usuario_atual)):
    """Retorna os dados do usuário autenticado."""
    return UsuarioSchema.model_validate(usuario)


@router.get(
    "/disciplinas",
    summary="📚 Disciplinas disponíveis",
)
async def listar_disciplinas():
    """Retorna as disciplinas disponíveis para cadastro e as áreas para filtro."""
    return {
        "disciplinas": DISCIPLINAS_VALIDAS,
        "areas": AREAS_DISCIPLINAS,
    }


@router.get(
    "/habilidades",
    response_model=HabilidadesFiltroResponse,
    summary="🔍 Listar assuntos (habilidades) para filtro",
)
async def listar_habilidades_filtro(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    pg_db: Session = Depends(get_db),
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de assuntos únicos (habilidade_id + habilidade_descricao)
    para popular o dropdown de filtros no frontend.
    Inclui quantidade de pendentes (cacheado por 5m).
    """
    cache_key = f"habilidades_filtro_{area}_{disciplina}_{usuario.id}"
    cached_data = get_from_cache(cache_key, ttl=300)
    if cached_data:
        return cached_data

    query = pg_db.query(
        HabilidadeModuloModel.habilidade_id, HabilidadeModuloModel.habilidade_descricao
    ).distinct()

    if area:
        query = query.filter(HabilidadeModuloModel.area == area)
    if disciplina:
        mapping = {
            "Artes": ["Artes", "Arte"],
            "Língua Inglesa": ["Língua Inglesa", "Inglês"],
            "Língua Portuguesa": [
                "Língua Portuguesa",
                "Lingua Portuguesa",
                "Literatura",
                "Redação",
            ],
        }
        mapped_names = mapping.get(disciplina, [disciplina])
        query = query.filter(HabilidadeModuloModel.disciplina.in_(mapped_names))

    results = query.order_by(HabilidadeModuloModel.habilidade_descricao).all()

    # Montar mapa de habilidades válidas
    hab_ids = [r.habilidade_id for r in results if r.habilidade_id is not None]

    # Mapa descrição (lowercase) → habilidade_id TRIEDUC
    desc_lower_to_trieduc: dict[str, int] = {}
    for r in results:
        if r.habilidade_id is not None and r.habilidade_descricao:
            desc_lower_to_trieduc[r.habilidade_descricao.lower()] = r.habilidade_id

    # Bridge: buscar IDs MySQL correspondentes via descrição (case-insensitive)
    # habilidade_modulos usa IDs TRIEDUC; questoes usa IDs MySQL — sistemas diferentes
    mysql_to_trieduc: dict[int, int] = {}
    if desc_lower_to_trieduc:
        mysql_hab_rows = (
            db.query(HabilidadeModel.id, HabilidadeModel.descricao)
            .filter(
                func.lower(HabilidadeModel.descricao).in_(
                    list(desc_lower_to_trieduc.keys())
                )
            )
            .all()
        )
        for mysql_id, mysql_desc in mysql_hab_rows:
            if mysql_desc:
                trieduc_id = desc_lower_to_trieduc.get(mysql_desc.lower())
                if trieduc_id:
                    mysql_to_trieduc[mysql_id] = trieduc_id

    # Fallback: só usar TRIEDUC ID como MySQL ID quando não houve mapeamento via descrição.
    # Se já existe um MySQL ID mapeado para este TRIEDUC ID, não adicionar o TRIEDUC ID
    # como MySQL ID extra (evita dupla contagem de questões com IDs distintos).
    trieduc_ids_ja_mapeados = set(mysql_to_trieduc.values())
    for trieduc_id in hab_ids:
        if trieduc_id not in trieduc_ids_ja_mapeados:
            mysql_to_trieduc[trieduc_id] = trieduc_id

    mysql_hab_ids = list(mysql_to_trieduc.keys())

    # IDs excluídos no PG (queries leves, sem IN gigante)
    ids_excluir: set[int] = set()

    # 2a. Já classificadas manualmente, com low-match, ou pelo SuperPro
    for r in (
        pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(
            (QuestaoAssuntoModel.classificado_manualmente == True)
            | (
                (QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None))
                & (
                    func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada)
                    > 0
                )
            )
            | (
                (QuestaoAssuntoModel.extracao_feita == True)
                & (QuestaoAssuntoModel.classificacoes.isnot(None))
                & (func.json_length(QuestaoAssuntoModel.classificacoes) > 0)
            )
        )
        .all()
    ):
        ids_excluir.add(r[0])

    # 2b. Já classificadas por este usuário
    for r in (
        pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    ):
        ids_excluir.add(r[0])

    # 2c. Puladas por qualquer usuário — só aparecem em Pendentes, nunca em /proxima
    for r in pg_db.query(QuestaoPuladaModel.questao_id).all():
        ids_excluir.add(r[0])

    counts_map: dict[int, int] = {}  # trieduc_id → pendentes
    if mysql_hab_ids:
        # Etapa 1: total de questões por habilidade MySQL (GROUP BY)
        rows_total = (
            db.query(
                QuestaoModel.habilidade_id,
                func.count(QuestaoModel.id).label("total"),
            )
            .filter(
                QuestaoModel.habilidade_id.in_(mysql_hab_ids),
                QuestaoModel.ano_id == 3,
            )
            .group_by(QuestaoModel.habilidade_id)
            .all()
        )

        # Agrupar por trieduc_id (vários MySQL IDs podem mapear para o mesmo)
        total_por_trieduc: dict[int, int] = {}
        for mysql_id, count in rows_total:
            trieduc_id = mysql_to_trieduc.get(mysql_id)
            if trieduc_id:
                total_por_trieduc[trieduc_id] = (
                    total_por_trieduc.get(trieduc_id, 0) + count
                )

        # Etapa 2: contagem de excluídas por habilidade no MySQL
        excluido_por_trieduc: dict[int, int] = {}
        if ids_excluir and total_por_trieduc:
            rows_excluido = (
                db.query(
                    QuestaoModel.habilidade_id,
                    func.count(QuestaoModel.id).label("excluidos"),
                )
                .filter(
                    QuestaoModel.id.in_(list(ids_excluir)),
                    QuestaoModel.habilidade_id.in_(mysql_hab_ids),
                    QuestaoModel.ano_id == 3,
                )
                .group_by(QuestaoModel.habilidade_id)
                .all()
            )
            for mysql_id, count in rows_excluido:
                trieduc_id = mysql_to_trieduc.get(mysql_id)
                if trieduc_id:
                    excluido_por_trieduc[trieduc_id] = (
                        excluido_por_trieduc.get(trieduc_id, 0) + count
                    )

        # Etapa 3: calcular pendentes (Python puro, O(n))
        for trieduc_id, total in total_por_trieduc.items():
            excluidos = excluido_por_trieduc.get(trieduc_id, 0)
            pendentes = total - excluidos
            if pendentes > 0:
                counts_map[trieduc_id] = pendentes

    if not counts_map:
        res = HabilidadesFiltroResponse(habilidades=[], total=0)
        set_to_cache(cache_key, res)
        return res

    habilidades = []
    for r in results:
        if r.habilidade_id is not None:
            pendentes = counts_map.get(r.habilidade_id, 0)
            if pendentes > 0:
                habilidades.append(
                    HabilidadeFiltroSchema(
                        habilidade_id=r.habilidade_id,
                        habilidade_descricao=r.habilidade_descricao,
                        pendentes=pendentes,
                    )
                )

    res = HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))
    set_to_cache(cache_key, res)
    return res


# ========================
# HABILIDADES PENDENTES (filtro da aba Pendentes)
# ========================


@router.get(
    "/habilidades-pendentes",
    response_model=HabilidadesFiltroResponse,
    summary="🔍 Assuntos com questões pendentes (puladas)",
)
async def listar_habilidades_pendentes(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna apenas os assuntos (habilidades) que possuem questões puladas (pendentes),
    com a contagem de quantas existem. Respeita o filtro de área/disciplina do usuário.
    """
    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    # IDs já classificados por este usuário (excluir das contagens)
    ids_classificadas: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # Base: questões puladas com habilidade definida
    query_puladas = pg_db.query(
        QuestaoPuladaModel.habilidade_id,
        func.count(func.distinct(QuestaoPuladaModel.questao_id)).label("total"),
    ).filter(QuestaoPuladaModel.habilidade_id.isnot(None))

    if ids_classificadas:
        query_puladas = query_puladas.filter(
            ~QuestaoPuladaModel.questao_id.in_(list(ids_classificadas))
        )

    # Filtro de disciplina explícito
    if disciplina:
        mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina, disciplina)
        if mysql_name:
            disc_id_row = (
                db.query(DisciplinaModel.id)
                .filter(DisciplinaModel.descricao == mysql_name)
                .first()
            )
            if disc_id_row:
                query_puladas = query_puladas.filter(
                    QuestaoPuladaModel.disciplina_id == disc_id_row[0]
                )
            else:
                return HabilidadesFiltroResponse(habilidades=[], total=0)
        else:
            habilidade_ids_custom = [
                row[0]
                for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                .filter(HabilidadeModuloModel.disciplina == disciplina)
                .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                .distinct()
                .all()
            ]
            if habilidade_ids_custom:
                query_puladas = query_puladas.filter(
                    QuestaoPuladaModel.habilidade_id.in_(habilidade_ids_custom)
                )
            else:
                return HabilidadesFiltroResponse(habilidades=[], total=0)
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        discs_ids = [
            d[0]
            for d in db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        ]
        if discs_ids:
            query_puladas = query_puladas.filter(
                QuestaoPuladaModel.disciplina_id.in_(discs_ids)
            )
    elif effective_area:
        query_puladas = query_puladas.filter(QuestaoPuladaModel.area == effective_area)

    rows = query_puladas.group_by(QuestaoPuladaModel.habilidade_id).all()
    if not rows:
        return HabilidadesFiltroResponse(habilidades=[], total=0)

    counts: dict[int, int] = {r[0]: r[1] for r in rows}
    hab_ids = list(counts.keys())

    # Buscar descrições no habilidade_modulos (PostgreSQL)
    desc_rows = (
        pg_db.query(
            HabilidadeModuloModel.habilidade_id,
            HabilidadeModuloModel.habilidade_descricao,
        )
        .filter(HabilidadeModuloModel.habilidade_id.in_(hab_ids))
        .distinct()
        .all()
    )

    # Mapa de descrições encontradas no HabilidadeModuloModel
    desc_map: dict[int, str] = {}
    for r in desc_rows:
        if r.habilidade_id not in desc_map:
            desc_map[r.habilidade_id] = r.habilidade_descricao

    # Fallback: buscar no MySQL (HabilidadeModel) os IDs que não existem em habilidade_modulos
    ids_sem_modulo = [hid for hid in hab_ids if hid not in desc_map]
    if ids_sem_modulo:
        fallback_rows = (
            db.query(HabilidadeModel.id, HabilidadeModel.descricao)
            .filter(HabilidadeModel.id.in_(ids_sem_modulo))
            .all()
        )
        for r in fallback_rows:
            if r.descricao:
                desc_map[r.id] = r.descricao

    habilidades = []
    for hab_id, descricao in sorted(desc_map.items(), key=lambda x: x[1]):
        habilidades.append(
            HabilidadeFiltroSchema(
                habilidade_id=hab_id,
                habilidade_descricao=descricao,
                pendentes=counts[hab_id],
            )
        )

    return HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))


@router.get(
    "/habilidades-verificar",
    response_model=HabilidadesFiltroResponse,
    summary="🔍 Assuntos com questões de baixa similaridade para verificar",
)
async def listar_habilidades_verificar(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna apenas assuntos TRIEDUC com pendências de verificação (low-match)."""
    cache_key = f"habilidades_verificar_{area}_{disciplina}_{usuario.id}"
    cached_data = get_from_cache(cache_key, ttl=300)
    if cached_data:
        return cached_data

    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    ids_verificadas_usuario: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    query_low_match_ids = pg_db.query(QuestaoAssuntoModel.questao_id).filter(
        QuestaoAssuntoModel.classificado_manualmente == False,
        QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None),
        func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0,
        QuestaoAssuntoModel.similaridade.isnot(None),
        QuestaoAssuntoModel.similaridade > 0,
        QuestaoAssuntoModel.similaridade < 0.8,
    )

    if ids_verificadas_usuario:
        query_low_match_ids = query_low_match_ids.filter(
            ~QuestaoAssuntoModel.questao_id.in_(list(ids_verificadas_usuario))
        )

    questao_ids_candidatas = [r[0] for r in query_low_match_ids.all()]
    if not questao_ids_candidatas:
        empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
        set_to_cache(cache_key, empty_res)
        return empty_res

    query_mysql = db.query(
        QuestaoModel.habilidade_id,
        func.count(func.distinct(QuestaoModel.id)).label("total"),
    ).filter(
        QuestaoModel.id.in_(questao_ids_candidatas),
        QuestaoModel.ano_id == 3,
        QuestaoModel.habilidade_id.isnot(None),
    )

    # Filtro por disciplina
    if disciplina:
        mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina, disciplina)
        if mysql_name:
            disc_id_row = (
                db.query(DisciplinaModel.id)
                .filter(DisciplinaModel.descricao == mysql_name)
                .first()
            )
            if not disc_id_row:
                empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
                set_to_cache(cache_key, empty_res)
                return empty_res
            query_mysql = query_mysql.filter(
                QuestaoModel.disciplina_id == disc_id_row[0]
            )
        else:
            habilidade_ids_custom = [
                row[0]
                for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                .filter(HabilidadeModuloModel.disciplina == disciplina)
                .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                .distinct()
                .all()
            ]
            if not habilidade_ids_custom:
                empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
                set_to_cache(cache_key, empty_res)
                return empty_res
            query_mysql = query_mysql.filter(
                QuestaoModel.habilidade_id.in_(habilidade_ids_custom)
            )
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        disciplinas_ids = [
            d[0]
            for d in db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        ]
        if not disciplinas_ids:
            empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
            set_to_cache(cache_key, empty_res)
            return empty_res
        query_mysql = query_mysql.filter(
            QuestaoModel.disciplina_id.in_(disciplinas_ids)
        )
    elif effective_area:
        habilidade_ids_area = [
            row[0]
            for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
            .filter(HabilidadeModuloModel.area == effective_area)
            .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
            .distinct()
            .all()
        ]
        if not habilidade_ids_area:
            empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
            set_to_cache(cache_key, empty_res)
            return empty_res
        query_mysql = query_mysql.filter(
            QuestaoModel.habilidade_id.in_(habilidade_ids_area)
        )

    rows = query_mysql.group_by(QuestaoModel.habilidade_id).all()
    if not rows:
        empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
        set_to_cache(cache_key, empty_res)
        return empty_res

    counts_map: dict[int, int] = {r[0]: int(r[1]) for r in rows if r[0] is not None}
    hab_ids = list(counts_map.keys())

    descricao_map: dict[int, str] = {}
    descricao_rows = (
        pg_db.query(
            HabilidadeModuloModel.habilidade_id,
            HabilidadeModuloModel.habilidade_descricao,
        )
        .filter(HabilidadeModuloModel.habilidade_id.in_(hab_ids))
        .distinct()
        .all()
    )
    for row in descricao_rows:
        if row.habilidade_id not in descricao_map and row.habilidade_descricao:
            descricao_map[row.habilidade_id] = row.habilidade_descricao

    faltantes = [hid for hid in hab_ids if hid not in descricao_map]
    if faltantes:
        fallback_rows = (
            db.query(HabilidadeModel.id, HabilidadeModel.descricao)
            .filter(HabilidadeModel.id.in_(faltantes))
            .all()
        )
        for hid, desc in fallback_rows:
            if hid not in descricao_map and desc:
                descricao_map[hid] = desc

    habilidades = [
        HabilidadeFiltroSchema(
            habilidade_id=hid,
            habilidade_descricao=descricao_map.get(hid, f"Habilidade {hid}"),
            pendentes=counts_map[hid],
        )
        for hid in sorted(
            hab_ids,
            key=lambda h: descricao_map.get(h, f"Habilidade {h}").lower(),
        )
    ]

    res = HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))
    set_to_cache(cache_key, res)
    return res


# ========================
# CONTAGEM POR DISCIPLINA (filas alta sim + confirmações)
# ========================


@router.get(
    "/contagem-filas",
    summary="📊 Contagem de pendentes por disciplina (alta similaridade e confirmações)",
)
async def contagem_filas_por_disciplina(
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna contagem de questões pendentes por disciplina para as duas filas:
    alta_similaridade (similaridade >= 0.8, não classificadas, sem 4 alt) e
    confirmacoes (tipo_acao='confirmacao' sem reclassificação posterior, sem 4 alt).
    """
    cache_key = "contagem_filas_v1"
    cached = get_from_cache(cache_key, ttl=120)
    if cached is not None:
        return cached

    # Alta sim: usa classificado_manualmente=0 (flag setado pelo fluxo trieduc),
    # excluindo adicionalmente os tipos genuinamente trieduc que não atualizam o flag.
    # NÃO exclui classificacao_superprofessor: esse tipo usa sp_id como questao_id,
    # que colide numericamente com trieduc IDs — seria exclusão indevida.
    rows_sim = db.execute(sql_text("""
        SELECT d.descricao, COUNT(*) AS total
        FROM thsethub.questao_assuntos qa
        JOIN trieduc.questoes q ON q.id = qa.questao_id
        JOIN trieduc.disciplinas d ON d.id = q.disciplina_id
        LEFT JOIN (
            SELECT questao_id, COUNT(*) AS n
            FROM trieduc.questao_alternativas GROUP BY questao_id
        ) alt ON alt.questao_id = q.id
        WHERE q.ano_id = 3
          AND q.habilidade_id IS NOT NULL
          AND qa.similaridade >= 0.8
          AND qa.classificado_manualmente = 0
          AND (alt.n IS NULL OR alt.n != 4)
          AND qa.questao_id NOT IN (
              SELECT questao_id FROM thsethub.classificacao_usuario
              WHERE tipo_acao IN ('classificacao_nova', 'correcao', 'classificacao_libro',
                                  'auto_classificacao')
          )
        GROUP BY d.descricao
    """)).fetchall()

    rows_conf = db.execute(sql_text("""
        SELECT d.descricao, COUNT(DISTINCT cu.questao_id) AS total
        FROM thsethub.classificacao_usuario cu
        JOIN trieduc.questoes q ON q.id = cu.questao_id
        JOIN trieduc.disciplinas d ON d.id = q.disciplina_id
        LEFT JOIN (
            SELECT questao_id, COUNT(*) AS n
            FROM trieduc.questao_alternativas GROUP BY questao_id
        ) alt ON alt.questao_id = q.id
        WHERE q.ano_id = 3
          AND q.habilidade_id IS NOT NULL
          AND cu.tipo_acao = 'confirmacao'
          AND cu.questao_id NOT IN (
              SELECT questao_id FROM thsethub.classificacao_usuario
              WHERE tipo_acao IN ('classificacao_nova', 'correcao', 'classificacao_libro',
                                  'auto_classificacao')
          )
          AND (alt.n IS NULL OR alt.n != 4)
        GROUP BY d.descricao
    """)).fetchall()

    result = {
        "alta_similaridade": {r[0]: r[1] for r in rows_sim},
        "confirmacoes": {r[0]: r[1] for r in rows_conf},
    }
    set_to_cache(cache_key, result)
    return result


# ========================
# MÓDULOS LIBRO DIRETO (compartilhados)
# ========================


@router.get(
    "/modulos-libro-direto",
    summary="📚 Módulos Libro com assuntos direto do banco compartilhados",
)
async def listar_modulos_libro_direto(
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna todos os módulos Libro com seus assuntos, diretamente das tabelas
    compartilhados.disciplinas_modulos e compartilhados.assuntos.
    Exclui itens prefixados com [RM]. Usado na classificação de alta similaridade.
    """
    cache_key = "modulos_libro_direto_v1"
    cached = get_from_cache(cache_key, ttl=600)
    if cached is not None:
        return cached

    rows = db.execute(sql_text("""
        SELECT
            d.disc_id,
            d.disc_descricao AS disciplina,
            dm.disc_modu_id,
            dm.disc_modu_descricao AS modulo,
            a.assu_id,
            a.assu_descricao AS assunto
        FROM compartilhados.disciplinas_modulos dm
        JOIN compartilhados.disciplinas d ON d.disc_id = dm.disc_id
        JOIN compartilhados.assuntos a ON a.disc_modu_id = dm.disc_modu_id
        WHERE dm.disc_modu_descricao NOT LIKE '[RM]%'
          AND a.assu_descricao NOT LIKE '[RM]%'
        ORDER BY d.disc_descricao, dm.disc_modu_descricao, a.assu_descricao
    """)).fetchall()

    # Montar estrutura: disciplina → módulos → assuntos
    disc_map: dict = {}
    for row in rows:
        disc_id = row[0]
        disciplina = row[1]
        disc_modu_id = row[2]
        modulo = row[3]
        assu_id = row[4]
        assunto = row[5]

        if disc_id not in disc_map:
            disc_map[disc_id] = {
                "disc_id": disc_id,
                "disciplina": disciplina,
                "modulos": {},
            }

        if disc_modu_id not in disc_map[disc_id]["modulos"]:
            disc_map[disc_id]["modulos"][disc_modu_id] = {
                "disc_modu_id": disc_modu_id,
                "modulo": modulo,
                "assuntos": [],
            }

        disc_map[disc_id]["modulos"][disc_modu_id]["assuntos"].append(
            {
                "assu_id": assu_id,
                "assunto": assunto,
            }
        )

    result = [
        {
            "disc_id": d["disc_id"],
            "disciplina": d["disciplina"],
            "modulos": list(d["modulos"].values()),
        }
        for d in disc_map.values()
    ]

    set_to_cache(cache_key, result)
    return result


# ========================
# ALTA SIMILARIDADE (>= 0.8)
# ========================


@router.get(
    "/assuntos-superpro",
    summary="📋 Assuntos SuperProfessor disponíveis (similaridade >= 0.8)",
)
async def listar_assuntos_superpro(
    disciplina_id: Optional[str] = Query(
        None, description="Nome da disciplina para filtrar"
    ),
    pg_db: Session = Depends(get_db),
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna assuntos únicos (primeiro elemento de classificacoes[]) das questões
    com similaridade >= 0.8, não classificadas manualmente, sem 4 alternativas.
    Usado para popular o dropdown de filtros na página de alta similaridade.
    """
    cache_key = f"assuntos_superpro_{disciplina_id}"
    cached = get_from_cache(cache_key, ttl=300)
    if cached is not None:
        return cached

    disc_mysql_id = None
    if disciplina_id:
        if str(disciplina_id).isdigit():
            disc_mysql_id = int(disciplina_id)
        else:
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            if mysql_name:
                disc_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                disc_mysql_id = disc_row[0] if disc_row else None

    disc_filter = f"AND q.disciplina_id = {disc_mysql_id}" if disc_mysql_id else ""

    raw_sql = sql_text(f"""
        SELECT
            JSON_UNQUOTE(JSON_EXTRACT(qa.classificacoes, '$[0]')) AS assunto,
            COUNT(*) AS total
        FROM thsethub.questao_assuntos qa
        JOIN trieduc.questoes q ON q.id = qa.questao_id
        LEFT JOIN (
            SELECT questao_id, COUNT(*) AS n_alt
            FROM trieduc.questao_alternativas
            GROUP BY questao_id
        ) alt_cnt ON alt_cnt.questao_id = qa.questao_id
        WHERE qa.similaridade >= 0.8
          AND qa.extracao_feita = 1
          AND qa.classificado_manualmente = 0
          AND JSON_LENGTH(qa.classificacoes) > 0
          AND (alt_cnt.n_alt IS NULL OR alt_cnt.n_alt != 4)
          {disc_filter}
        GROUP BY assunto
        HAVING assunto IS NOT NULL AND assunto != 'null'
        ORDER BY total DESC
        LIMIT 500
    """)

    rows = db.execute(raw_sql).fetchall()
    result = [{"assunto": r[0], "total": r[1]} for r in rows]
    set_to_cache(cache_key, result)
    return result


@router.get(
    "/assuntos-superpro-confirmacoes",
    summary="📋 Assuntos SuperProfessor disponíveis na fila de confirmações",
)
async def listar_assuntos_superpro_confirmacoes(
    disciplina_id: Optional[str] = Query(
        None, description="Nome da disciplina para filtrar"
    ),
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna assuntos únicos (primeiro elemento de classificacoes[]) das questões
    que estão na fila de confirmações (confirmadas sem módulos libro).
    """
    cache_key = f"assuntos_confirmacoes_v2_{disciplina_id}"
    cached = get_from_cache(cache_key, ttl=300)
    if cached is not None:
        return cached

    disc_mysql_id = None
    if disciplina_id:
        if str(disciplina_id).isdigit():
            disc_mysql_id = int(disciplina_id)
        else:
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            if mysql_name:
                disc_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                disc_mysql_id = disc_row[0] if disc_row else None

    disc_filter = f"AND q.disciplina_id = {disc_mysql_id}" if disc_mysql_id else ""

    from sqlalchemy import text as sql_text

    raw_sql = sql_text(f"""
        SELECT
            COALESCE(
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(qa.classificacoes, '$[0]')), 'null'),
                h.descricao
            ) AS assunto,
            COUNT(DISTINCT cu.questao_id) AS total
        FROM thsethub.classificacao_usuario cu
        JOIN trieduc.questoes q ON q.id = cu.questao_id
        LEFT JOIN thsethub.questao_assuntos qa ON qa.questao_id = cu.questao_id
        LEFT JOIN trieduc.habilidades h ON h.id = q.habilidade_id
        LEFT JOIN (
            SELECT questao_id, COUNT(*) AS n_alt
            FROM trieduc.questao_alternativas
            GROUP BY questao_id
        ) alt_cnt ON alt_cnt.questao_id = cu.questao_id
        WHERE cu.tipo_acao = 'confirmacao'
          AND cu.questao_id NOT IN (
              SELECT questao_id FROM thsethub.classificacao_usuario
              WHERE tipo_acao IN ('classificacao_nova', 'correcao', 'classificacao_libro', 'auto_classificacao')
          )
          AND (alt_cnt.n_alt IS NULL OR alt_cnt.n_alt != 4)
          {disc_filter}
        GROUP BY assunto
        HAVING assunto IS NOT NULL AND assunto != ''
        ORDER BY total DESC
        LIMIT 500
    """)

    rows = db.execute(raw_sql).fetchall()
    result = [{"assunto": r[0], "total": r[1]} for r in rows]
    set_to_cache(cache_key, result)
    return result


@router.get(
    "/proxima-alta-similaridade",
    response_model=QuestaoClassifResponse,
    summary="🔍 Próxima questão com similaridade >= 0.8 para classificar",
)
async def proxima_questao_alta_similaridade(
    assunto_superpro: Optional[str] = Query(
        None, description="Filtrar pelo primeiro assunto superprofessor"
    ),
    disciplina_id: Optional[str] = Query(None, description="Nome ou ID da disciplina"),
    last_questao_id: Optional[int] = Query(
        0, description="Último questao_id visto (seek)"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna a próxima questão com similaridade >= 0.8 ainda não classificada manualmente.
    Exclui questões com 4 alternativas (múltipla escolha com 4 opções).
    """
    LIMIT_CANDIDATES = 100
    MAX_LOOP_TRIES = 50

    disc_mysql_id = None
    if disciplina_id:
        if str(disciplina_id).isdigit():
            disc_mysql_id = int(disciplina_id)
        else:
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            if mysql_name:
                disc_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                disc_mysql_id = disc_row[0] if disc_row else None

    last_id = last_questao_id or 0

    # IDs que o usuário atual já pulou — serão ignorados na fila
    puladas_usuario = {
        r[0]
        for r in pg_db.query(QuestaoPuladaModel.questao_id)
        .filter(QuestaoPuladaModel.usuario_id == usuario.id)
        .all()
    }

    qa_query = (
        pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(QuestaoAssuntoModel.similaridade >= 0.8)
        .filter(QuestaoAssuntoModel.extracao_feita == True)
        .filter(QuestaoAssuntoModel.classificado_manualmente == False)
        .filter(QuestaoAssuntoModel.classificacoes.isnot(None))
        .filter(func.json_length(QuestaoAssuntoModel.classificacoes) > 0)
    )

    if assunto_superpro:
        qa_query = qa_query.filter(
            func.json_unquote(
                func.json_extract(QuestaoAssuntoModel.classificacoes, "$[0]")
            )
            == assunto_superpro
        )

    qa_query = qa_query.order_by(QuestaoAssuntoModel.questao_id)

    questao_final = None

    for _ in range(MAX_LOOP_TRIES):
        candidates_qa = (
            qa_query.filter(QuestaoAssuntoModel.questao_id > last_id)
            .limit(LIMIT_CANDIDATES)
            .all()
        )
        if not candidates_qa:
            break

        candidate_ids = [c[0] for c in candidates_qa]
        last_id = candidate_ids[-1]

        # Filtrar por disciplina no MySQL
        if disc_mysql_id:
            valid_mysql = {
                r[0]
                for r in db.query(QuestaoModel.id)
                .filter(QuestaoModel.id.in_(candidate_ids))
                .filter(QuestaoModel.disciplina_id == disc_mysql_id)
                .all()
            }
            candidate_ids = [c for c in candidate_ids if c in valid_mysql]

        if not candidate_ids:
            continue

        # Excluir questões com 4 alternativas
        from ..database.models import QuestaoAlternativaModel

        alt_counts = {
            r[0]: r[1]
            for r in db.query(
                QuestaoAlternativaModel.questao_id,
                func.count(QuestaoAlternativaModel.id),
            )
            .filter(QuestaoAlternativaModel.questao_id.in_(candidate_ids))
            .group_by(QuestaoAlternativaModel.questao_id)
            .all()
        }
        candidate_ids = [c for c in candidate_ids if alt_counts.get(c, 0) != 4]

        # Excluir questões que o usuário já pulou
        if puladas_usuario:
            candidate_ids = [c for c in candidate_ids if c not in puladas_usuario]

        if not candidate_ids:
            continue

        valid_id = candidate_ids[0]

        questao_final = (
            db.query(QuestaoModel)
            .options(
                joinedload(QuestaoModel.disciplina),
                joinedload(QuestaoModel.habilidade),
                joinedload(QuestaoModel.alternativas),
            )
            .filter(QuestaoModel.id == valid_id)
            .first()
        )

        if questao_final:
            break

    if not questao_final:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão de alta similaridade pendente encontrada.",
        )

    questao = questao_final
    enunciado_tratado, _, _ = tratar_enunciado(questao.enunciado)
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None
    hab_descricao = questao.habilidade.descricao if questao.habilidade else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado,
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=extracao.classificacoes if extracao else None,
        classificacao_nao_enquadrada=(
            extracao.classificacao_nao_enquadrada if extracao else None
        ),
        similaridade=extracao.similaridade if extracao else None,
        tem_extracao=bool(extracao and extracao.extracao_feita),
        modulos_possiveis=[],
    )


@router.get(
    "/proxima-confirmacao",
    response_model=QuestaoClassifResponse,
    summary="🔁 Próxima questão com apenas confirmação (sem módulos libro)",
)
async def proxima_questao_confirmacao(
    disciplina_id: Optional[str] = Query(None, description="Nome ou ID da disciplina"),
    assunto_superpro: Optional[str] = Query(
        None, description="Filtrar pelo primeiro assunto superprofessor"
    ),
    last_questao_id: Optional[int] = Query(
        0, description="Último questao_id visto (seek)"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna questões que foram apenas 'confirmadas' sem módulos libro selecionados,
    e que não tiveram reclassificação posterior (classificacao_nova ou correcao).
    Exclui questões com 4 alternativas.
    """
    LIMIT_CANDIDATES = 100
    MAX_LOOP_TRIES = 50

    disc_mysql_id = None
    if disciplina_id:
        if str(disciplina_id).isdigit():
            disc_mysql_id = int(disciplina_id)
        else:
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            if mysql_name:
                disc_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                disc_mysql_id = disc_row[0] if disc_row else None

    # IDs com reclassificação posterior (excluir) — inclui classificacao_libro
    reclassificados = {
        r[0]
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_nova", "correcao", "classificacao_libro"]
            )
        )
        .all()
    }

    # IDs que o usuário atual já pulou — serão ignorados na fila
    puladas_usuario = {
        r[0]
        for r in pg_db.query(QuestaoPuladaModel.questao_id)
        .filter(QuestaoPuladaModel.usuario_id == usuario.id)
        .all()
    }

    # Pré-filtro por assunto — tenta classificacoes[0] e fallback por habilidade.descricao
    assunto_ids_filter = None
    if assunto_superpro:
        # Via classificacoes[0] em questao_assuntos
        ids_via_classificacoes = {
            r[0]
            for r in pg_db.query(QuestaoAssuntoModel.questao_id)
            .filter(
                func.json_unquote(
                    func.json_extract(QuestaoAssuntoModel.classificacoes, "$[0]")
                )
                == assunto_superpro
            )
            .all()
        }
        # Via habilidade.descricao (para questões sem classificacao SP)
        hab_rows = (
            db.query(HabilidadeModel.id)
            .filter(HabilidadeModel.descricao == assunto_superpro)
            .all()
        )
        ids_via_habilidade: set[int] = set()
        if hab_rows:
            hab_ids = [r[0] for r in hab_rows]
            ids_via_habilidade = {
                r[0]
                for r in db.query(QuestaoModel.id)
                .filter(QuestaoModel.habilidade_id.in_(hab_ids))
                .all()
            }
        assunto_ids_filter = ids_via_classificacoes | ids_via_habilidade

    # IDs confirmados (candidatos)
    confirmados_query = (
        pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "confirmacao")
        .filter(ClassificacaoUsuarioModel.questao_id.notin_(reclassificados))
        .distinct()
        .order_by(ClassificacaoUsuarioModel.questao_id)
    )

    if assunto_ids_filter is not None:
        confirmados_query = confirmados_query.filter(
            ClassificacaoUsuarioModel.questao_id.in_(list(assunto_ids_filter))
        )

    last_id = last_questao_id or 0
    questao_final = None

    for _ in range(MAX_LOOP_TRIES):
        candidates = (
            confirmados_query.filter(ClassificacaoUsuarioModel.questao_id > last_id)
            .limit(LIMIT_CANDIDATES)
            .all()
        )
        if not candidates:
            break

        candidate_ids = [c[0] for c in candidates]
        last_id = candidate_ids[-1]

        # Filtrar por disciplina no MySQL
        if disc_mysql_id:
            valid_mysql = {
                r[0]
                for r in db.query(QuestaoModel.id)
                .filter(QuestaoModel.id.in_(candidate_ids))
                .filter(QuestaoModel.disciplina_id == disc_mysql_id)
                .all()
            }
            candidate_ids = [c for c in candidate_ids if c in valid_mysql]

        if not candidate_ids:
            continue

        # Excluir questões com 4 alternativas
        from ..database.models import QuestaoAlternativaModel

        alt_counts = {
            r[0]: r[1]
            for r in db.query(
                QuestaoAlternativaModel.questao_id,
                func.count(QuestaoAlternativaModel.id),
            )
            .filter(QuestaoAlternativaModel.questao_id.in_(candidate_ids))
            .group_by(QuestaoAlternativaModel.questao_id)
            .all()
        }
        candidate_ids = [c for c in candidate_ids if alt_counts.get(c, 0) != 4]

        # Excluir questões que o usuário já pulou
        if puladas_usuario:
            candidate_ids = [c for c in candidate_ids if c not in puladas_usuario]

        if not candidate_ids:
            continue

        valid_id = candidate_ids[0]

        questao_final = (
            db.query(QuestaoModel)
            .options(
                joinedload(QuestaoModel.disciplina),
                joinedload(QuestaoModel.habilidade),
                joinedload(QuestaoModel.alternativas),
            )
            .filter(QuestaoModel.id == valid_id)
            .first()
        )

        if questao_final:
            break

    if not questao_final:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão de confirmação pendente encontrada.",
        )

    questao = questao_final
    enunciado_tratado, _, _ = tratar_enunciado(questao.enunciado)
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None
    hab_descricao = questao.habilidade.descricao if questao.habilidade else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado,
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=extracao.classificacoes if extracao else None,
        classificacao_nao_enquadrada=(
            extracao.classificacao_nao_enquadrada if extracao else None
        ),
        similaridade=extracao.similaridade if extracao else None,
        tem_extracao=bool(extracao and extracao.extracao_feita),
        modulos_possiveis=[],
    )


# ========================
# MÓDULOS (consulta)
# ========================


@router.get(
    "/modulos",
    response_model=List[HabilidadeModuloSchema],
    summary="📦 Todos os módulos disponíveis para seleção manual",
)
async def listar_todos_modulos(
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    area: Optional[str] = Query(None, description="Filtrar por área"),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna todos os módulos do TriEduc, opcionalmente filtrados por disciplina ou área.
    Usado no modal de correção de classificação para permitir busca livre por qualquer módulo.
    """
    cache_key = f"todos_modulos_{area}_{disciplina}"
    cached = get_from_cache(cache_key, ttl=600)
    if cached is not None:
        return cached

    query = pg_db.query(HabilidadeModuloModel)
    if area:
        query = query.filter(HabilidadeModuloModel.area == area)
    if disciplina:
        query = query.filter(HabilidadeModuloModel.disciplina == disciplina)

    modulos = query.order_by(
        HabilidadeModuloModel.area,
        HabilidadeModuloModel.disciplina,
        HabilidadeModuloModel.modulo,
        HabilidadeModuloModel.descricao,
    ).all()
    result = [HabilidadeModuloSchema.model_validate(m) for m in modulos]
    set_to_cache(cache_key, result)
    return result


@router.get(
    "/modulos-assuntos",
    response_model=ModulosAssuntosResponse,
    summary="📚 Módulos com assuntos relacionados (sem prefixo [RM])",
)
async def listar_modulos_com_assuntos(
    shared_db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna módulos do banco compartilhados com os assuntos relacionados válidos.

    Retorna apenas os módulos do LibroStudio sem relacionamento atual com o TriEduc.
    """
    cache_key = "modulos_assuntos_compartilhados_v4"
    cached = get_from_cache(cache_key, ttl=600)
    if cached is not None:
        return cached

    try:
        inspector = inspect(shared_db.get_bind())
        table_names = inspector.get_table_names(schema="compartilhados")
        table_names_lower = {t.lower(): t for t in table_names}

        assuntos_table = None
        for candidate in ["assuntos", "assunto"]:
            if candidate in table_names_lower:
                assuntos_table = table_names_lower[candidate]
                break

        modulos_table = None
        for candidate in [
            "disciplina_modulos",
            "disciplinas_modulos",
            "disciplina_modulo",
            "disciplinas_modulo",
            "modulos_disciplina",
            "modulo_disciplina",
        ]:
            if candidate in table_names_lower:
                modulos_table = table_names_lower[candidate]
                break

        if not modulos_table:
            for t in table_names:
                t_norm = t.lower()
                if "modul" in t_norm and "disc" in t_norm:
                    modulos_table = t
                    break

        disciplinas_table = None
        for candidate in ["disciplinas", "disciplina"]:
            if candidate in table_names_lower:
                disciplinas_table = table_names_lower[candidate]
                break

        if not disciplinas_table:
            for t in table_names:
                t_norm = t.lower()
                if "disciplina" in t_norm and "modul" not in t_norm:
                    disciplinas_table = t
                    break

        if not assuntos_table:
            raise HTTPException(
                status_code=500,
                detail="Tabela de assuntos não encontrada em compartilhados.",
            )
        if not modulos_table:
            raise HTTPException(
                status_code=500,
                detail="Tabela de módulos de disciplina não encontrada em compartilhados.",
            )

        assuntos_cols = {
            c["name"]
            for c in inspector.get_columns(assuntos_table, schema="compartilhados")
        }
        modulos_cols = {
            c["name"]
            for c in inspector.get_columns(modulos_table, schema="compartilhados")
        }
        disciplinas_cols = (
            {
                c["name"]
                for c in inspector.get_columns(
                    disciplinas_table, schema="compartilhados"
                )
            }
            if disciplinas_table
            else set()
        )

        assunto_desc_col = _pick_first_column(
            assuntos_cols, ["assu_descricao", "descricao", "nome"]
        )
        if not assunto_desc_col:
            raise HTTPException(
                status_code=500,
                detail="Coluna de descrição de assunto não encontrada na tabela 'assuntos'.",
            )

        assunto_id_col = _pick_first_column(assuntos_cols, ["assu_id", "id"])

        modulo_nome_col = _pick_first_column(
            modulos_cols,
            [
                "modulo",
                "dimo_descricao",
                "descricao",
                "nome",
                "disc_modu_descricao",
                "disc_modulo_descricao",
                "disc_modu_nome",
                "disc_modulo_nome",
                "dimo_nome",
            ],
        )
        if not modulo_nome_col:
            modulo_nome_col = next(
                (
                    c
                    for c in modulos_cols
                    if "modu" in c.lower()
                    and (
                        "descr" in c.lower()
                        or "nome" in c.lower()
                        or "titulo" in c.lower()
                    )
                ),
                None,
            )
        if not modulo_nome_col:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Coluna de nome de módulo não encontrada. "
                    f"Colunas disponíveis: {sorted(modulos_cols)}"
                ),
            )

        disciplina_nome_col = _pick_first_column(
            modulos_cols,
            [
                "disciplina",
                "disciplina_nome",
                "disciplina_descricao",
                "nome_disciplina",
            ],
        )
        if not disciplina_nome_col:
            disciplina_nome_col = next(
                (
                    c
                    for c in modulos_cols
                    if c != modulo_nome_col
                    and "disc" in c.lower()
                    and ("nome" in c.lower() or "desc" in c.lower())
                ),
                None,
            )

        disciplina_id_fk_col = _pick_first_column(
            modulos_cols,
            [
                "disc_id",
                "disciplina_id",
                "id_disciplina",
                "disciplinaid",
                "id_disc",
            ],
        )
        disciplina_id_col = _pick_first_column(
            disciplinas_cols,
            [
                "disc_id",
                "id",
                "disciplina_id",
                "id_disciplina",
            ],
        )
        if not disciplina_id_col and disciplinas_cols:
            disciplina_id_col = next(
                (
                    c
                    for c in disciplinas_cols
                    if "disc" in c.lower() and c.lower().endswith("id")
                ),
                None,
            )
        if not disciplina_id_col and disciplinas_cols:
            disciplina_id_col = next(
                (c for c in disciplinas_cols if c.lower().endswith("id")),
                None,
            )
        disciplina_nome_from_table_col = (
            _pick_first_column(
                disciplinas_cols,
                [
                    "disc_descricao",
                    "disc_nome",
                    "descricao",
                    "nome",
                    "disciplina_nome",
                    "nome_disciplina",
                ],
            )
            if disciplinas_cols
            else None
        )
        if not disciplina_nome_from_table_col and disciplinas_cols:
            disciplina_nome_from_table_col = next(
                (
                    c
                    for c in disciplinas_cols
                    if "disc" in c.lower()
                    and ("desc" in c.lower() or "nome" in c.lower())
                ),
                None,
            )

        join_disciplinas = bool(
            disciplinas_table
            and disciplina_id_fk_col
            and disciplina_id_col
            and disciplina_nome_from_table_col
        )

        join_candidates = [
            ("disc_modu_id", "disc_modu_id"),
            ("disciplina_modulo_id", "id"),
            ("dimo_id", "dimo_id"),
            ("dimo_id", "id"),
            ("disc_modu_id", "id"),
        ]
        join_cols = next(
            (
                (a_col, dm_col)
                for a_col, dm_col in join_candidates
                if a_col in assuntos_cols and dm_col in modulos_cols
            ),
            None,
        )
        if not join_cols:
            raise HTTPException(
                status_code=500,
                detail="Não foi possível identificar o relacionamento entre 'assuntos' e 'disciplina_modulos'.",
            )

        assunto_join_col, modulo_join_col = join_cols
        modulo_id_col = (
            _pick_first_column(
                modulos_cols,
                [
                    modulo_join_col,
                    "disc_modu_id",
                    "dimo_id",
                    "id",
                ],
            )
            or modulo_join_col
        )
        modulo_disc_modu_col = _pick_first_column(
            modulos_cols,
            [
                modulo_join_col,
                "disc_modu_id",
                "dimo_id",
                "id",
            ],
        )

        assunto_id_select = (
            f", a.{_sql_ident(assunto_id_col)} AS assunto_id" if assunto_id_col else ""
        )
        modulo_disc_modu_select = (
            f", dm.{_sql_ident(modulo_disc_modu_col)} AS modulo_disc_modu_id"
            if modulo_disc_modu_col
            else ""
        )

        disciplina_nome_select = ""
        disciplina_id_select = ", NULL AS disciplina_id"
        disciplina_join_sql = ""
        if join_disciplinas:
            disciplina_nome_select = (
                f", d.{_sql_ident(disciplina_nome_from_table_col)} AS disciplina_nome"
            )
            disciplina_id_select = (
                f", dm.{_sql_ident(disciplina_id_fk_col)} AS disciplina_id"
            )
            disciplina_join_sql = f"""
                LEFT JOIN compartilhados.{_sql_ident(disciplinas_table)} d
                    ON dm.{_sql_ident(disciplina_id_fk_col)} = d.{_sql_ident(disciplina_id_col)}
            """
        else:
            disciplina_nome_select = (
                f", dm.{_sql_ident(disciplina_nome_col)} AS disciplina_nome"
                if disciplina_nome_col
                else ", NULL AS disciplina_nome"
            )

        trieduc_pairs = set()
        trieduc_disc_modu_ids = set()
        trieduc_triplets = set()  # (disciplina, modulo, assunto)

        for disciplina, modulo, disc_modu_id, assunto_descricao in pg_db.query(
            HabilidadeModuloModel.disciplina,
            HabilidadeModuloModel.modulo,
            HabilidadeModuloModel.disc_modu_id,
            HabilidadeModuloModel.descricao,
        ).all():
            disc_norm = _normalize_text(disciplina)
            mod_norm = _normalize_text(modulo)
            assunto_norm = _normalize_text(assunto_descricao)

            if disc_norm and mod_norm:
                trieduc_pairs.add((disc_norm, mod_norm))

            if disc_norm and mod_norm and assunto_norm:
                trieduc_triplets.add((disc_norm, mod_norm, assunto_norm))

            disc_modu_norm = _normalize_disc_modu_id(disc_modu_id)
            if disc_modu_norm:
                trieduc_disc_modu_ids.add(disc_modu_norm)

        sql = f"""
            SELECT
                dm.{_sql_ident(modulo_id_col)} AS modulo_id,
                dm.{_sql_ident(modulo_nome_col)} AS modulo_nome,
                a.{_sql_ident(assunto_desc_col)} AS assunto_descricao
                {assunto_id_select}
                {disciplina_id_select}
                {disciplina_nome_select}
                {modulo_disc_modu_select}
            FROM compartilhados.{_sql_ident(assuntos_table)} a
            INNER JOIN compartilhados.{_sql_ident(modulos_table)} dm
                ON a.{_sql_ident(assunto_join_col)} = dm.{_sql_ident(modulo_join_col)}
            {disciplina_join_sql}
            WHERE a.{_sql_ident(assunto_desc_col)} IS NOT NULL
                AND TRIM(a.{_sql_ident(assunto_desc_col)}) <> ''
                AND TRIM(a.{_sql_ident(assunto_desc_col)}) NOT LIKE :rm_prefix
            ORDER BY dm.{_sql_ident(modulo_nome_col)}, a.{_sql_ident(assunto_desc_col)}
        """

        rows = shared_db.execute(sql_text(sql), {"rm_prefix": "[RM]%"}).mappings().all()

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao consultar módulos/assuntos em compartilhados: {exc}",
        ) from exc

    grouped = {}
    for row in rows:
        modulo_nome = (row.get("modulo_nome") or "").strip()
        if not modulo_nome:
            continue

        disciplina_nome = (row.get("disciplina_nome") or "").strip()
        disciplina_id = row.get("disciplina_id")
        disciplina_norm = _normalize_text(disciplina_nome)
        modulo_norm = _normalize_text(modulo_nome)

        modulo_disc_modu_norm = _normalize_disc_modu_id(row.get("modulo_disc_modu_id"))

        # Não pular mais o módulo inteiro aqui
        # has_relacionamento_trieduc = False
        # if disciplina_norm and modulo_norm and (disciplina_norm, modulo_norm) in trieduc_pairs:
        #     has_relacionamento_trieduc = True
        # elif modulo_disc_modu_norm and modulo_disc_modu_norm in trieduc_disc_modu_ids:
        #     has_relacionamento_trieduc = True
        #
        # if has_relacionamento_trieduc:
        #     continue

        modulo_id = (
            row.get("modulo_id") if row.get("modulo_id") is not None else modulo_nome
        )
        disciplina_id_key = _normalize_disc_modu_id(disciplina_id)
        group_key = (
            f"{disciplina_id_key or disciplina_norm}::{modulo_id}::{modulo_norm}"
        )

        if group_key not in grouped:
            grouped[group_key] = {
                "id": modulo_id,
                "disciplina_id": disciplina_id,
                "nome": modulo_nome,
                "disciplina": disciplina_nome,
                "assuntos": [],
                "_seen": set(),
            }

        assunto_descricao = (row.get("assunto_descricao") or "").strip()
        if not assunto_descricao:
            continue

        # Verificar se este par (módulo + assunto) específico já existe no relacionamento trieduc
        assunto_norm = _normalize_text(assunto_descricao)
        triplet_exists = (
            disciplina_norm,
            modulo_norm,
            assunto_norm,
        ) in trieduc_triplets

        if triplet_exists:
            # Pular apenas este assunto específico, não o módulo inteiro
            continue

        assunto_id = row.get("assunto_id")
        assunto_key = (assunto_id, assunto_descricao)
        if assunto_key in grouped[group_key]["_seen"]:
            continue

        grouped[group_key]["_seen"].add(assunto_key)
        grouped[group_key]["assuntos"].append(
            AssuntoVinculadoSchema(
                id=assunto_id,
                descricao=assunto_descricao,
            )
        )

    modulos = []
    total_assuntos = 0
    for module_data in grouped.values():
        assuntos = module_data["assuntos"]

        # Não incluir módulos que ficaram sem assuntos após filtrar
        if not assuntos:
            continue

        total_assuntos += len(assuntos)
        modulos.append(
            ModuloComAssuntosSchema(
                id=module_data["id"],
                disciplina_id=module_data["disciplina_id"],
                disciplina=module_data["disciplina"],
                nome=module_data["nome"],
                assuntos=assuntos,
                total_assuntos=len(assuntos),
                fonte="librostudio",
                has_relacionamento_trieduc=False,
            )
        )

    modulos.sort(
        key=lambda m: (
            str(m.disciplina_id or ""),
            (m.disciplina or "").lower(),
            m.nome.lower(),
        )
    )

    response = ModulosAssuntosResponse(
        modulos=modulos,
        total_modulos=len(modulos),
        total_assuntos=total_assuntos,
    )
    set_to_cache(cache_key, response)
    return response


@router.get(
    "/modulos/{habilidade_id}",
    response_model=ModulosResponse,
    summary="📦 Módulos possíveis para uma habilidade",
)
async def listar_modulos_por_habilidade(
    habilidade_id: int,
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna os módulos possíveis para um dado habilidade_id do TriEduc."""
    modulos = (
        pg_db.query(HabilidadeModuloModel)
        .filter(HabilidadeModuloModel.habilidade_id == habilidade_id)
        .order_by(
            HabilidadeModuloModel.area,
            HabilidadeModuloModel.disciplina,
            HabilidadeModuloModel.modulo,
        )
        .all()
    )

    return ModulosResponse(
        habilidade_id=habilidade_id,
        modulos=[HabilidadeModuloSchema.model_validate(m) for m in modulos],
        total=len(modulos),
    )


# ========================
# QUESTÕES PARA CLASSIFICAR
# ========================


@router.get(
    "/proxima",
    response_model=QuestaoClassifResponse,
    summary="🔍 Próxima questão para classificar",
)
async def proxima_questao_classificar(
    area: Optional[str] = Query(
        None, description="Filtrar por área (Humanas, Linguagens, Matemática, Natureza)"
    ),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão que ainda NÃO foi classificada manualmente pelo usuário.
    Prioriza questões sem extração automática.

    Filtros:
    - **area**: "Humanas", "Linguagens", "Matemática", "Natureza"
    - **disciplina_id**: ID numérico da disciplina
    - **habilidade_id**: ID da habilidade TRIEDUC
    """
    # IDs a excluir (já classificadas por este usuário OU já possuem classificação no sistema)
    # Forçar área do usuário se não enviada
    if not area:
        area = usuario.disciplina

    logger.info(
        f"Busca Próxima: usuario={usuario.nome}, area={area}, disciplina={disciplina_id}, habilidade={habilidade_id}"
    )

    # Resolver filtro de área → disciplinas (Otimizado: apenas IDs)
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[area]
        discs_ids = (
            db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        )
        disciplina_ids_filtro = [d[0] for d in discs_ids]

    # OPTIMIZATION: 'Seek Method' (ID > last_id) instead of OFFSET.
    # This is much faster for large tables as it uses the Primary Key index directly.

    LIMIT_CANDIDATES = 200
    MAX_LOOP_TRIES = 50  # Total candidates to check = 10,000

    # Base query for candidate IDs in MySQL
    # We select ONLY the ID to keep it lightweight
    candidate_query = (
        db.query(QuestaoModel.id)
        .filter(QuestaoModel.habilidade_id.isnot(None))
        .filter(QuestaoModel.ano_id == 3)  # Ensino Médio
    )

    if habilidade_id:
        # Resolver TRIEDUC habilidade_id → MySQL habilidade_id(s) via descrição.
        # Os dois sistemas usam IDs diferentes; sem esta resolução o filtro retorna
        # zero questões mesmo quando o dropdown mostra pendentes.
        resolved_mysql_ids = _resolver_habilidade_mysql_ids(habilidade_id, pg_db, db)
        candidate_query = candidate_query.filter(
            QuestaoModel.habilidade_id.in_(resolved_mysql_ids)
        )

    if disciplina_id:
        if str(disciplina_id).isdigit():
            candidate_query = candidate_query.filter(
                QuestaoModel.disciplina_id == int(disciplina_id)
            )
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)

            if mysql_name:
                disc_id_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                if disc_id_row:
                    candidate_query = candidate_query.filter(
                        QuestaoModel.disciplina_id == disc_id_row[0]
                    )
                else:
                    # Se nome exato não existe no MySQL, falhar para não mostrar tudo
                    candidate_query = candidate_query.filter(QuestaoModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0]
                    for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    candidate_query = candidate_query.filter(
                        QuestaoModel.habilidade_id.in_(habilidade_ids_custom)
                    )
                else:
                    candidate_query = candidate_query.filter(QuestaoModel.id == -1)
    elif disciplina_ids_filtro:
        candidate_query = candidate_query.filter(
            QuestaoModel.disciplina_id.in_(disciplina_ids_filtro)
        )

    # Pre-filter: exclui questões já processadas no sistema usando NOT EXISTS cross-schema.
    # Evita o loop de 50 iterações quando todas as questões já estão em questao_assuntos.
    # pg_db e db usam o mesmo servidor MySQL (thsethub e trieduc são schemas diferentes).
    candidate_query = candidate_query.filter(
        sql_text(
            "NOT EXISTS ("
            "  SELECT 1 FROM thsethub.questao_assuntos qa_pre"
            "  WHERE qa_pre.questao_id = questoes.id"
            "  AND ("
            "    qa_pre.classificado_manualmente = 1"
            "    OR (qa_pre.classificacao_nao_enquadrada IS NOT NULL"
            "        AND JSON_LENGTH(qa_pre.classificacao_nao_enquadrada) > 0)"
            "    OR (qa_pre.extracao_feita = 1"
            "        AND qa_pre.classificacoes IS NOT NULL"
            "        AND JSON_LENGTH(qa_pre.classificacoes) > 0)"
            "  )"
            ")"
        )
    )

    candidate_query = candidate_query.order_by(QuestaoModel.id)

    last_id = 0
    questao_final = None

    for _ in range(MAX_LOOP_TRIES):
        # Fetch next block of candidate IDs starting from last_id
        candidates = (
            candidate_query.filter(QuestaoModel.id > last_id)
            .limit(LIMIT_CANDIDATES)
            .all()
        )
        if not candidates:
            break

        candidate_ids = [c[0] for c in candidates]
        last_id = candidate_ids[-1]  # Update for next possible loop

        # Check in PostgreSQL which candidates of THIS block are already processed
        # 1. Already classified by this user
        classified_by_user = {
            row[0]
            for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
            .filter(ClassificacaoUsuarioModel.questao_id.in_(candidate_ids))
            .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
            .all()
        }

        # classified_in_system: substituído pelo NOT EXISTS pre-filter na candidate_query.
        classified_in_system = set()

        # 3. Puladas por qualquer usuário — só aparecem na aba Pendentes, nunca em /proxima
        skipped_any_user = {
            row[0]
            for row in pg_db.query(QuestaoPuladaModel.questao_id)
            .filter(QuestaoPuladaModel.questao_id.in_(candidate_ids))
            .all()
        }

        ids_excluir = classified_by_user.union(classified_in_system).union(
            skipped_any_user
        )

        # Find first candidate not in exclude set
        valid_id = None
        for cid in candidate_ids:
            if cid not in ids_excluir:
                valid_id = cid
                break

        if valid_id:
            # Fetch FULL details only for the 1 question found
            questao_final = (
                db.query(QuestaoModel)
                .options(
                    joinedload(QuestaoModel.disciplina),
                    joinedload(QuestaoModel.habilidade),
                    joinedload(QuestaoModel.alternativas),
                )
                .filter(QuestaoModel.id == valid_id)
                .first()
            )

            if questao_final:
                # Basic text check
                enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(
                    questao_final.enunciado
                )
                if not motivo_erro:
                    # Success!
                    break
                else:
                    # Mark as invalid in PG so we don't try it again
                    if valid_id not in classified_in_system:
                        disc_nome = (
                            questao_final.disciplina.descricao
                            if questao_final.disciplina
                            else None
                        )
                        reg = QuestaoAssuntoModel(
                            questao_id=questao_final.id,
                            questao_id_str=questao_final.questao_id,
                            disciplina_id=questao_final.disciplina_id,
                            disciplina_nome=disc_nome,
                            classificacoes=[],
                            enunciado_original=questao_final.enunciado,
                            enunciado_tratado=enunciado_tratado,
                            extracao_feita=False,
                            contem_imagem=contem_imagem,
                            motivo_erro=motivo_erro,
                        )
                        pg_db.add(reg)
                        pg_db.commit()
                    questao_final = None  # Keep looking in the same or next block

    if not questao_final:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão pendente para classificação encontrada.",
        )

    # Re-use details
    questao = questao_final
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # Check for suggested extraction to display
    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    hab_descricao = None
    if questao.habilidade:
        hab_descricao = questao.habilidade.descricao

    # Módulos possíveis
    modulos = []
    if questao.habilidade_id:
        from .classificacao_schemas import HabilidadeModuloSchema

        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        # FALLBACK: Se não achou módulos por ID, tenta por descrição (Case Insensitive)
        if not modulos and hab_descricao:
            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(
                    func.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado,
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=(
            extracao.classificacoes if extracao and extracao.extracao_feita else None
        ),
        tem_extracao=bool(
            extracao and extracao.extracao_feita and extracao.classificacoes
        ),
        modulos_possiveis=modulos,
    )


@router.get(
    "/consulta/{questao_id}",
    response_model=QuestaoClassifResponse,
    summary="Consultar questão por ID (admin)",
)
async def consultar_questao_por_id(
    questao_id: int,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna uma questão específica no mesmo formato da rota /proxima."""
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")

    questao = (
        db.query(QuestaoModel)
        .options(
            joinedload(QuestaoModel.disciplina),
            joinedload(QuestaoModel.habilidade),
            joinedload(QuestaoModel.alternativas),
        )
        .filter(QuestaoModel.id == questao_id)
        .first()
    )
    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    classificacao_manual = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(
            ClassificacaoUsuarioModel.questao_id == questao.id,
            ClassificacaoUsuarioModel.usuario_id != 0,
        )
        .order_by(
            ClassificacaoUsuarioModel.created_at.desc(),
            ClassificacaoUsuarioModel.id.desc(),
        )
        .first()
    )

    hab_descricao = None
    if questao.habilidade:
        hab_descricao = questao.habilidade.descricao

    modulos = []
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        if not modulos and hab_descricao:
            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(
                    func.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    manual_payload = None
    if classificacao_manual:
        manual_modulos = classificacao_manual.modulos_escolhidos or (
            [classificacao_manual.modulo_escolhido]
            if classificacao_manual.modulo_escolhido
            else []
        )
        manual_descricoes = classificacao_manual.descricoes_assunto_list or (
            [classificacao_manual.descricao_assunto]
            if classificacao_manual.descricao_assunto
            else []
        )
        manual_payload = ClassificacaoManualResumoSchema(
            usuario_id=classificacao_manual.usuario_id,
            tipo_acao=classificacao_manual.tipo_acao,
            modulos=[m for m in manual_modulos if m],
            descricoes=[d for d in manual_descricoes if d],
            observacao=classificacao_manual.observacao,
            created_at=classificacao_manual.created_at,
        )

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado or "",
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=(
            extracao.classificacoes if extracao and extracao.extracao_feita else None
        ),
        classificacao_nao_enquadrada=(
            extracao.classificacao_nao_enquadrada
            if extracao and extracao.classificacao_nao_enquadrada
            else None
        ),
        similaridade=extracao.similaridade if extracao else None,
        tem_extracao=bool(
            extracao and extracao.extracao_feita and extracao.classificacoes
        ),
        classificacao_manual=manual_payload,
        modulos_possiveis=modulos,
    )


@router.get(
    "/proxima-verificar",
    response_model=QuestaoClassifResponse,
    summary="🔄 Próxima questão para verificar (já classificada)",
)
async def proxima_questao_verificar(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão que JÁ tem classificação automática
    para o usuário verificar se está correta.
    """
    # IDs já verificadas por este usuário
    ids_verificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # Forçar área do usuário se não enviada
    if not area:
        area = usuario.disciplina

    # Resolver filtro de área
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        from ..database.models import DisciplinaModel

        nomes = AREAS_DISCIPLINAS[area]
        discs = (
            db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        )
        disciplina_ids_filtro = [d.id for d in discs]

    # Query Base no PG: extraídas pelo Superpro com baixa similaridade (precisa verificação humana)
    query_pg = pg_db.query(QuestaoAssuntoModel).filter(
        QuestaoAssuntoModel.extracao_feita == True,
        QuestaoAssuntoModel.classificacoes.isnot(None),
        QuestaoAssuntoModel.similaridade > 0,
        QuestaoAssuntoModel.similaridade < 0.8,
    )

    if habilidade_id:
        # Resolver TRIEDUC habilidade_id → MySQL habilidade_id(s)
        resolved_mysql_ids = _resolver_habilidade_mysql_ids(habilidade_id, pg_db, db)
        questao_ids_habilidade = [
            row[0]
            for row in db.query(QuestaoModel.id)
            .filter(
                QuestaoModel.habilidade_id.in_(resolved_mysql_ids),
                QuestaoModel.ano_id == 3,
            )
            .all()
        ]
        if questao_ids_habilidade:
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id.in_(questao_ids_habilidade)
            )
        else:
            query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)

    if ids_verificadas:
        query_pg = query_pg.filter(~QuestaoAssuntoModel.questao_id.in_(ids_verificadas))

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.disciplina_id == int(disciplina_id)
            )
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)

            disc_target_id = None
            if mysql_name:
                disc = (
                    db.query(DisciplinaModel)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                if disc:
                    disc_target_id = disc.id

            if disc_target_id:
                query_pg = query_pg.filter(
                    QuestaoAssuntoModel.disciplina_id == disc_target_id
                )
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0]
                    for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    # No QuestaoAssuntoModel, habilidade_id pode não estar preenchido se veio do scraping
                    # Mas se tivermos o ID TRIEDUC no MySQL (QuestaoModel), podemos filtrar lá.
                    # No entanto, a query base é sobre QuestaoAssuntoModel.
                    # Se salvamos a extração, populamos habilidade_id? Geralmente sim.
                    questao_ids_custom = [
                        row[0]
                        for row in db.query(QuestaoModel.id)
                        .filter(
                            QuestaoModel.habilidade_id.in_(habilidade_ids_custom),
                            QuestaoModel.ano_id == 3,
                        )
                        .all()
                    ]
                    if questao_ids_custom:
                        query_pg = query_pg.filter(
                            QuestaoAssuntoModel.questao_id.in_(questao_ids_custom)
                        )
                    else:
                        query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)
                else:
                    query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)
    elif disciplina_ids_filtro:
        query_pg = query_pg.filter(
            QuestaoAssuntoModel.disciplina_id.in_(disciplina_ids_filtro)
        )

    # Tentar encontrar uma questão que seja efetivamente de Ensino Médio no MySQL
    MAX_TENTATIVAS = 100
    for _ in range(MAX_TENTATIVAS):
        registro_pg = query_pg.order_by(QuestaoAssuntoModel.id).first()

        if not registro_pg:
            raise HTTPException(
                status_code=404,
                detail="Nenhuma questão pendente de verificação com os filtros aplicados",
            )

        # Verificar nível no MySQL
        questao = (
            db.query(QuestaoModel)
            .options(
                joinedload(QuestaoModel.disciplina),
                joinedload(QuestaoModel.habilidade),
                joinedload(QuestaoModel.alternativas),
            )
            .filter(QuestaoModel.id == registro_pg.questao_id)
            .first()
        )

        if not questao or questao.ano_id != 3:
            # Pula esta e marca como "inválida para este fluxo" temporariamente na query
            ids_verificadas.add(registro_pg.questao_id)
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id != registro_pg.questao_id
            )
            continue

        # Se chegou aqui, temos a questão!
        break
    else:
        raise HTTPException(
            status_code=404,
            detail="Não foram encontradas questões de Ensino Médio para verificar.",
        )

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # Módulos possíveis
    modulos = []
    hab_descricao = None
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        from ..database.models import HabilidadeModel

        hab = (
            db.query(HabilidadeModel)
            .filter(HabilidadeModel.id == questao.habilidade_id)
            .first()
        )
        if hab:
            hab_descricao = hab.descricao

        # FALLBACK: Se não achou módulos por ID, tenta por descrição (Case Insensitive)
        if not modulos and hab_descricao:
            from sqlalchemy import func

            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(
                    func.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    # Alternativas
    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado or "",
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=registro_pg.classificacoes,
        tem_extracao=True,
        modulos_possiveis=modulos,
    )


# ========================
# PRÓXIMA QUESTÃO LOW MATCH
# ========================


@router.get(
    "/proxima-low-match",
    response_model=QuestaoClassifResponse,
    summary="⚠️ Próxima questão com classificação de baixa similaridade",
)
async def proxima_questao_low_match(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão que possui classificacao_nao_enquadrada
    (match baixo do SuperProfessor) para revisão pelo professor.
    """
    # IDs já verificadas por este usuário
    ids_verificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # Forçar área do usuário se não enviada
    if not area:
        area = usuario.disciplina

    # Resolver filtro de área
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        from ..database.models import DisciplinaModel

        nomes = AREAS_DISCIPLINAS[area]
        discs = (
            db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        )
        disciplina_ids_filtro = [d.id for d in discs]

    # Query no PG: questões com classificacao_nao_enquadrada preenchida
    query_pg = pg_db.query(QuestaoAssuntoModel).filter(
        QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None),
        func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0,
        QuestaoAssuntoModel.classificado_manualmente == False,
        QuestaoAssuntoModel.similaridade.isnot(None),
        QuestaoAssuntoModel.similaridade > 0,
        QuestaoAssuntoModel.similaridade < 0.8,
    )

    if habilidade_id:
        # Resolver TRIEDUC habilidade_id → MySQL habilidade_id(s)
        resolved_mysql_ids = _resolver_habilidade_mysql_ids(habilidade_id, pg_db, db)
        questao_ids_habilidade = [
            row[0]
            for row in db.query(QuestaoModel.id)
            .filter(
                QuestaoModel.habilidade_id.in_(resolved_mysql_ids),
                QuestaoModel.ano_id == 3,
            )
            .all()
        ]
        if questao_ids_habilidade:
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id.in_(questao_ids_habilidade)
            )
        else:
            query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)

    if ids_verificadas:
        query_pg = query_pg.filter(~QuestaoAssuntoModel.questao_id.in_(ids_verificadas))

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.disciplina_id == int(disciplina_id)
            )
        else:
            from ..database.models import DisciplinaModel

            disc = (
                db.query(DisciplinaModel)
                .filter(DisciplinaModel.descricao == disciplina_id)
                .first()
            )
            if disc:
                query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id == disc.id)
    elif disciplina_ids_filtro:
        query_pg = query_pg.filter(
            QuestaoAssuntoModel.disciplina_id.in_(disciplina_ids_filtro)
        )

    # Tentar encontrar uma questão válida de Ensino Médio
    MAX_TENTATIVAS = 100
    for _ in range(MAX_TENTATIVAS):
        registro_pg = query_pg.order_by(QuestaoAssuntoModel.id).first()

        if not registro_pg:
            raise HTTPException(
                status_code=404,
                detail="Nenhuma questão de baixa similaridade pendente com os filtros aplicados",
            )

        # Verificar nível no MySQL
        questao = (
            db.query(QuestaoModel)
            .options(
                joinedload(QuestaoModel.disciplina),
                joinedload(QuestaoModel.habilidade),
                joinedload(QuestaoModel.alternativas),
            )
            .filter(QuestaoModel.id == registro_pg.questao_id)
            .first()
        )

        if not questao or questao.ano_id != 3:
            ids_verificadas.add(registro_pg.questao_id)
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id != registro_pg.questao_id
            )
            continue

        break
    else:
        raise HTTPException(
            status_code=404,
            detail="Não foram encontradas questões de baixa similaridade para verificar.",
        )

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # Módulos possíveis
    modulos = []
    hab_descricao = None
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        from ..database.models import HabilidadeModel

        hab = (
            db.query(HabilidadeModel)
            .filter(HabilidadeModel.id == questao.habilidade_id)
            .first()
        )
        if hab:
            hab_descricao = hab.descricao

        if not modulos and hab_descricao:
            from sqlalchemy import func as sqlfunc

            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(
                    sqlfunc.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    # Alternativas
    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado or "",
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=registro_pg.classificacoes,
        classificacao_nao_enquadrada=registro_pg.classificacao_nao_enquadrada,
        similaridade=registro_pg.similaridade,
        tem_extracao=bool(registro_pg.classificacoes),
        modulos_possiveis=modulos,
    )


# ========================
# SALVAR CLASSIFICAÇÃO
# ========================


@router.post(
    "/salvar",
    response_model=SalvarClassificacaoResponse,
    summary="💾 Salvar classificação do usuário",
)
async def salvar_classificacao(
    request: SalvarClassificacaoRequest,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Salva a decisão de classificação do usuário.
    Tipos de ação:
    - **classificacao_nova**: Questão que não tinha classificação
    - **confirmacao**: Usuário confirmou classificação existente
    - **correcao**: Usuário corrigiu classificação existente
    - **classificacao_libro**: Classificação realizada pelo sistema Libro
    """
    if request.tipo_acao not in (
        "classificacao_nova",
        "confirmacao",
        "correcao",
        "classificacao_libro",
        "classificacao_superprofessor",
    ):
        raise HTTPException(status_code=400, detail="tipo_acao inválido")

    # Buscar habilidade_id da questão (Apenas o necessário)
    questao_data = (
        db.query(
            QuestaoModel.id,
            QuestaoModel.habilidade_id,
            QuestaoModel.questao_id,
            QuestaoModel.disciplina_id,
        )
        .filter(QuestaoModel.id == request.questao_id)
        .first()
    )
    if not questao_data:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    # Buscar classificação da extração (se existir)
    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == request.questao_id)
        .first()
    )

    # 1. Atualizar flag de classificação manual na tabela questao_assuntos
    if not extracao:
        # Buscar nome da disciplina se for criar
        from ..database.models import DisciplinaModel

        disc_nome = None
        if questao_data.disciplina_id:
            disc_row = (
                db.query(DisciplinaModel.descricao)
                .filter(DisciplinaModel.id == questao_data.disciplina_id)
                .first()
            )
            disc_nome = disc_row[0] if disc_row else None

        # Criar registro básico para marcar como manual
        extracao = QuestaoAssuntoModel(
            questao_id=questao_data.id,
            questao_id_str=questao_data.questao_id,
            disciplina_id=questao_data.disciplina_id,
            disciplina_nome=disc_nome,
            classificacoes=[],
            classificado_manualmente=True,
        )
        pg_db.add(extracao)
    else:
        extracao.classificado_manualmente = True

    # Criar registro de histórico
    classificacao = ClassificacaoUsuarioModel(
        usuario_id=usuario.id,
        questao_id=request.questao_id,
        habilidade_id=questao_data.habilidade_id,
        # Campos legados (single) - retrocompatibilidade
        modulo_escolhido=request.modulo_escolhido,
        classificacao_trieduc=request.classificacao_trieduc,
        descricao_assunto=request.descricao_assunto,
        habilidade_modulo_id=request.habilidade_modulo_id,
        # Campos novos (múltiplos módulos JSONB)
        modulos_escolhidos=request.modulos_escolhidos,
        classificacoes_trieduc_list=request.classificacoes_trieduc,
        descricoes_assunto_list=request.descricoes_assunto,
        habilidade_modulo_ids=request.habilidade_modulo_ids,
        # Extração e metadados
        classificacao_extracao=extracao.classificacoes if extracao else None,
        tipo_acao=request.tipo_acao,
        observacao=request.observacao,
    )
    pg_db.add(classificacao)

    # Auto-remover da lista de questões puladas (se existir para qualquer usuário)
    # Motivo: Se foi classificada, não está mais pendente para ninguém.
    pg_db.query(QuestaoPuladaModel).filter(
        QuestaoPuladaModel.questao_id == request.questao_id,
    ).delete()

    pg_db.commit()

    modulos_info = (
        request.modulos_escolhidos or [request.modulo_escolhido]
        if request.modulo_escolhido
        else []
    )
    logger.info(
        f"Classificação salva: usuario={usuario.nome}, questao={request.questao_id}, "
        f"acao={request.tipo_acao}, modulos={modulos_info}"
    )

    return SalvarClassificacaoResponse(
        success=True,
        id=classificacao.id,
        questao_id=request.questao_id,
        tipo_acao=request.tipo_acao,
        message=f"Classificação ({request.tipo_acao}) salva com sucesso",
    )


# ========================
# PULAR QUESTÃO (PENDENTES)
# ========================


@router.post(
    "/pular",
    response_model=PularQuestaoResponse,
    summary="⏭️ Pular questão (marcar como pendente)",
)
async def pular_questao(
    request: PularQuestaoRequest,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Marca uma questão como pulada pelo usuário.
    A questão aparecerá na aba 'Pendentes' para classificação posterior.
    """
    # Verificar se a questão existe
    questao_data = (
        db.query(
            QuestaoModel.id, QuestaoModel.disciplina_id, QuestaoModel.habilidade_id
        )
        .filter(QuestaoModel.id == request.questao_id)
        .first()
    )

    if not questao_data:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    # Verificar se já foi pulada (evitar duplicata)
    existente = (
        pg_db.query(QuestaoPuladaModel)
        .filter(
            QuestaoPuladaModel.usuario_id == usuario.id,
            QuestaoPuladaModel.questao_id == request.questao_id,
        )
        .first()
    )

    if existente:
        return PularQuestaoResponse(
            success=True,
            message="Questão já estava marcada como pendente",
        )

    # Registrar como pulada
    pulada = QuestaoPuladaModel(
        usuario_id=usuario.id,
        questao_id=request.questao_id,
        area=usuario.disciplina,
        disciplina_id=questao_data.disciplina_id,
        habilidade_id=questao_data.habilidade_id,
    )
    pg_db.add(pulada)
    pg_db.commit()

    logger.info(f"Questão pulada: usuario={usuario.nome}, questao={request.questao_id}")

    return PularQuestaoResponse(
        success=True,
        message="Questão marcada como pendente",
    )


@router.get(
    "/proxima-pendente",
    response_model=QuestaoClassifResponse,
    summary="📋 Próxima questão pendente (pulada)",
)
async def proxima_questao_pendente(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão pendente (pulada por qualquer usuário).
    Restrito à área/disciplina do usuário por padrão.
    """
    # Base query: questões puladas por qualquer usuário
    query_puladas = pg_db.query(QuestaoPuladaModel)

    # Área efetiva: usa o filtro explícito, ou cai na disciplina do próprio usuário (não-admin)
    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    # Aplicar filtros
    if habilidade_id:
        query_puladas = query_puladas.filter(
            QuestaoPuladaModel.habilidade_id == habilidade_id
        )

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_puladas = query_puladas.filter(
                QuestaoPuladaModel.disciplina_id == int(disciplina_id)
            )
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)

            if mysql_name:
                disc_id_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                if disc_id_row:
                    query_puladas = query_puladas.filter(
                        QuestaoPuladaModel.disciplina_id == disc_id_row[0]
                    )
                else:
                    # Se não existe no MySQL, falhar filtro
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0]
                    for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    query_puladas = query_puladas.filter(
                        QuestaoPuladaModel.habilidade_id.in_(habilidade_ids_custom)
                    )
                else:
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.id == -1)
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        discs_ids = (
            db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        )
        disciplina_ids_filtro = [d[0] for d in discs_ids]
        if disciplina_ids_filtro:
            query_puladas = query_puladas.filter(
                QuestaoPuladaModel.disciplina_id.in_(disciplina_ids_filtro)
            )
    elif effective_area:
        # Fallback: filtrar diretamente pelo campo area salvo no momento do pulo
        query_puladas = query_puladas.filter(QuestaoPuladaModel.area == effective_area)

    # LOG para depuração
    logger.info(
        f"Filtro Pendentes: usuario={usuario.nome}, effective_area={effective_area}, disciplina={disciplina_id}"
    )
    count_antes = query_puladas.count()
    logger.info(f"Total pendentes com filtros aplicados: {count_antes}")

    # IDs já classificadas por este usuário (excluir das pendentes)
    ids_classificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    if ids_classificadas:
        query_puladas = query_puladas.filter(
            ~QuestaoPuladaModel.questao_id.in_(ids_classificadas)
        )

    # Buscar próxima pendente (ordem de inserção)
    registro_pulado = query_puladas.order_by(QuestaoPuladaModel.id).first()

    if not registro_pulado:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão pendente encontrada com os filtros aplicados.",
        )

    # Carregar detalhes completos da questão do MySQL
    questao = (
        db.query(QuestaoModel)
        .options(
            joinedload(QuestaoModel.disciplina),
            joinedload(QuestaoModel.habilidade),
            joinedload(QuestaoModel.alternativas),
        )
        .filter(QuestaoModel.id == registro_pulado.questao_id)
        .first()
    )

    if not questao:
        # Questão não existe mais no MySQL, remover da lista de puladas
        pg_db.delete(registro_pulado)
        pg_db.commit()
        raise HTTPException(
            status_code=404, detail="Questão pendente não encontrada no banco de dados."
        )

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # Verificar classificação existente
    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    hab_descricao = None
    if questao.habilidade:
        hab_descricao = questao.habilidade.descricao

    # Módulos possíveis
    modulos = []
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        # FALLBACK por descrição
        if not modulos and hab_descricao:
            from sqlalchemy import func as sqlfunc

            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(
                    sqlfunc.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    # Alternativas
    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado or "",
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=(
            extracao.classificacoes if extracao and extracao.extracao_feita else None
        ),
        tem_extracao=bool(
            extracao and extracao.extracao_feita and extracao.classificacoes
        ),
        modulos_possiveis=modulos,
    )


# ========================
# ESTATÍSTICAS
# ========================


@router.get(
    "/stats",
    response_model=ClassificacaoStatsResponse,
    summary="📊 Estatísticas de classificação manual",
)
async def estatisticas_classificacao(
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna estatísticas do sistema de classificação manual (sem cache)."""

    total = pg_db.query(ClassificacaoUsuarioModel).count()
    novas = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_nova")
        .count()
    )
    confirmacoes = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "confirmacao")
        .count()
    )
    correcoes = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "correcao")
        .count()
    )
    usuarios_ativos = (
        pg_db.query(UsuarioModel).filter(UsuarioModel.ativo == True).count()
    )

    # Filtro Base: Ensino Médio + Habilidade ID
    # Join com DisciplinaModel para garantir integridade (opcional mas mantido para consistência)
    from ..database.models import DisciplinaModel

    em_query = db.query(QuestaoModel.id).filter(
        QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None)
    )
    em_ids = [r[0] for r in em_query.all()]
    total_sistema = len(em_ids)

    if not em_ids:
        res = ClassificacaoStatsResponse(
            total_sistema=0, por_usuario={}, por_disciplina={}
        )
        return res

    # 0. Questões com 4 alternativas — excluídas do funil de classificação
    from ..database.models import QuestaoAlternativaModel as _QAlt

    quatro_alt_ids = {
        r[0]
        for r in db.query(_QAlt.questao_id)
        .filter(_QAlt.questao_id.in_(em_ids))
        .group_by(_QAlt.questao_id)
        .having(func.count(_QAlt.id) == 4)
        .all()
    }
    total_4_alternativas = len(quatro_alt_ids)

    # Funil elegível = EM com habilidade, sem 4 alternativas
    eligible_ids = list(set(em_ids) - quatro_alt_ids)
    total_sistema = len(eligible_ids)

    # ================================================================
    # CLASSIFICAÇÃO EM BUCKETS MUTUAMENTE EXCLUSIVOS
    # ================================================================
    # Ordem de prioridade (uma vez em um bucket, não entra em outro):
    #   1. MANUAIS (classificadas/finalizadas)
    #   2. CONFIRMAÇÕES (confirmadas sem libro — aguardam módulos)
    #   3. ALTA SIMILARIDADE (sim ≥ 80% sem ação)
    #   4. FALTAM VERIFICAR (0 < sim < 80% sem ação)
    #   5. PULADAS
    #   6. PENDENTES (resto: sem dados de similaridade)
    # ================================================================
    eligible_set = set(eligible_ids)

    TIPO_ACAO_FINALIZADO = [
        "classificacao_nova",
        "correcao",
        "classificacao_libro",
        "auto_classificacao",
    ]

    # Pré-carrega ações por questão (uma query, sem IN gigante)
    # IMPORTANTE: classificacao_superprofessor / pular_superprofessor usam sp_id
    # (base questoes_superprofessor — universo separado), e são processadas
    # separadamente abaixo. Aqui filtramos apenas ações do fluxo trieduc.
    acoes_por_questao: dict[int, set[str]] = {}
    for qid, ta in (
        pg_db.query(
            ClassificacaoUsuarioModel.questao_id, ClassificacaoUsuarioModel.tipo_acao
        )
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.notin_(
                ["classificacao_superprofessor", "pular_superprofessor"]
            )
        )
        .all()
    ):
        if qid in eligible_set:
            acoes_por_questao.setdefault(qid, set()).add(ta)

    # 1. MANUAIS — questão finalizada no fluxo trieduc
    manuais_via_acao: set[int] = {
        qid
        for qid, acoes in acoes_por_questao.items()
        if acoes & set(TIPO_ACAO_FINALIZADO)
    }
    # Captura questões com classificado_manualmente=1 cujo tipo_acao registrado
    # não está em TIPO_ACAO_FINALIZADO (tipicamente colisões com sp_id classificadas
    # via SP). Excluímos as que têm confirmacao (essas devem ir para a fila de
    # confirmações pendentes, não para manuais).
    confirmacao_ids_raw = {
        qid for qid, acoes in acoes_por_questao.items() if "confirmacao" in acoes
    }
    manuais_via_flag = {
        r[0]
        for r in pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(QuestaoAssuntoModel.classificado_manualmente == True)
        .all()
    } & eligible_set
    manuais_via_flag -= confirmacao_ids_raw
    manuais_ids = manuais_via_acao | manuais_via_flag
    total_manuais = len(manuais_ids)

    # 2. CONFIRMAÇÕES PENDENTES — tem tipo_acao='confirmacao' mas não é manuais
    confirmacoes_ids = {
        qid for qid, acoes in acoes_por_questao.items() if "confirmacao" in acoes
    } - manuais_ids
    total_confirmacoes_pendentes = len(confirmacoes_ids)

    # 3. ALTA SIMILARIDADE — sim ≥ 0.8 sem ação finalizadora nem confirmação
    alta_sim_raw = {
        r[0]
        for r in pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(QuestaoAssuntoModel.similaridade >= 0.8)
        .all()
    } & eligible_set
    alta_sim_ids = alta_sim_raw - manuais_ids - confirmacoes_ids
    total_alta_similaridade = len(alta_sim_ids)
    total_auto_superpro = 0  # retrocompat

    # 4. FALTAM VERIFICAR — 0 < sim < 0.8 sem ação nem confirmação nem alta_sim
    verificar_raw = {
        r[0]
        for r in pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(
            QuestaoAssuntoModel.similaridade < 0.8,
            QuestaoAssuntoModel.similaridade > 0,
        )
        .all()
    } & eligible_set
    verificar_ids = verificar_raw - manuais_ids - confirmacoes_ids - alta_sim_ids
    total_precisa_verificar = len(verificar_ids)

    # 5. PULADAS
    from ..database.pg_pular_models import QuestaoPuladaModel

    puladas_raw = {
        r[0] for r in pg_db.query(QuestaoPuladaModel.questao_id).all()
    } & eligible_set
    puladas_ids_disjoint = (
        puladas_raw - manuais_ids - confirmacoes_ids - alta_sim_ids - verificar_ids
    )
    total_puladas = len(puladas_ids_disjoint)

    # 6. PENDENTES (resto matemático — garantido ≥ 0 pela construção disjunta)
    total_pendentes = max(
        0,
        total_sistema
        - total_manuais
        - total_confirmacoes_pendentes
        - total_alta_similaridade
        - total_precisa_verificar
        - total_puladas,
    )

    # ================================================================
    # BASE SUPERPROFESSOR (universo separado em thsethub.questoes_superprofessor)
    # IDs (sp_id) são independentes dos IDs trieduc — somamos no total global
    # e o tooltip explica a contribuição de cada base.
    # ================================================================
    from ..database.pg_usuario_models import QuestaoSuperprofessorModel as _QSP

    # Mapeamento dos nomes SP → nomes consolidados (trieduc)
    sp_nome_map = {
        "Inglês": "Língua Inglesa",
        "Espanhol": "Espanhol",
        "Arte": "Artes",
        "Literatura": "Língua Portuguesa",
        "Redação": "Língua Portuguesa",
    }

    # SP por disciplina: total, classificadas, puladas
    sp_rows = pg_db.query(_QSP.sp_id, _QSP.disciplina_sp).all()
    sp_por_disc_total: dict[str, int] = {}
    sp_id_to_disc: dict[int, str] = {}
    for sp_id, disc_sp in sp_rows:
        disc_norm = sp_nome_map.get(disc_sp, disc_sp) if disc_sp else "—"
        sp_por_disc_total[disc_norm] = sp_por_disc_total.get(disc_norm, 0) + 1
        sp_id_to_disc[sp_id] = disc_norm

    sp_classif_ids = {
        r[0]
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_superprofessor")
        .distinct()
        .all()
    }
    sp_pulada_ids = {
        r[0]
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "pular_superprofessor")
        .distinct()
        .all()
    }

    sp_por_disc_classif: dict[str, int] = {}
    sp_por_disc_pulada: dict[str, int] = {}
    for sp_id, disc in sp_id_to_disc.items():
        if sp_id in sp_classif_ids:
            sp_por_disc_classif[disc] = sp_por_disc_classif.get(disc, 0) + 1
        elif sp_id in sp_pulada_ids:
            sp_por_disc_pulada[disc] = sp_por_disc_pulada.get(disc, 0) + 1

    total_superprofessor = len(sp_id_to_disc)
    total_superprofessor_classificadas = sum(sp_por_disc_classif.values())
    total_superprofessor_puladas = sum(sp_por_disc_pulada.values())
    total_superprofessor_pendentes = max(
        0,
        total_superprofessor
        - total_superprofessor_classificadas
        - total_superprofessor_puladas,
    )

    # Por disciplina (Dashboard style)
    mysql_rows = (
        db.query(QuestaoModel.disciplina_id, func.count(QuestaoModel.id))
        .filter(QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None))
        .group_by(QuestaoModel.disciplina_id)
        .all()
    )
    mysql_counts = {r[0]: r[1] for r in mysql_rows}

    # Feitas = apenas as classificadas manualmente (alta sim pendente não conta como feita)
    ids_finalizados = manuais_ids

    # Mapa questao_id → disciplina_id (MySQL) para detalhar por disciplina
    q_disc_rows = (
        db.query(QuestaoModel.id, QuestaoModel.disciplina_id)
        .filter(QuestaoModel.id.in_(em_ids))
        .all()
    )
    disc_ids_map: dict[int, set] = {}  # disciplina_id → set de questao_ids
    for qid, did in q_disc_rows:
        if did is None:
            continue
        disc_ids_map.setdefault(did, set()).add(qid)

    disc_names = {d.id: d.descricao for d in db.query(DisciplinaModel).all()}

    # Contagem de módulos e assuntos por disciplina (banco compartilhados)
    from sqlalchemy import text as sql_text

    try:
        # Query SQL exata fornecida pelo usuário
        sql = """
            SELECT
                d.disc_id,
                d.disc_descricao,
                COUNT(DISTINCT dm.disc_modu_id) AS total_modulos,
                COUNT(a.assu_id) AS total_assuntos
            FROM compartilhados.disciplinas d
            INNER JOIN compartilhados.disciplinas_modulos dm
                ON dm.disc_id = d.disc_id
            LEFT JOIN compartilhados.assuntos a
                ON a.disc_modu_id = dm.disc_modu_id
               AND TRIM(a.assu_descricao) NOT LIKE '[RM]%%'
            WHERE TRIM(dm.disc_modu_descricao) NOT LIKE '[RM]%%'
            GROUP BY d.disc_id, d.disc_descricao
            ORDER BY d.disc_id
        """

        result = db.execute(sql_text(sql)).fetchall()

        logger.info(f"Total de linhas retornadas da query: {len(result)}")

        # Mapeamento de sinônimos entre bancos (case-insensitive)
        nome_sinonimos = {
            "inglês": "Língua Inglesa",
            "espanhol": "Língua Espanhola",
            "arte": "Artes",
        }

        # Processa resultados e soma Literatura + Redação com Língua Portuguesa
        modulos_por_disc = {}
        assuntos_por_disc = {}

        # Primeiro passo: coleta todos os valores
        lingua_port_modulos = 0
        lingua_port_assuntos = 0

        for row in result:
            disc_descricao_orig = row[1]
            disc_descricao = disc_descricao_orig.strip() if disc_descricao_orig else ""
            total_modulos = row[2] or 0
            total_assuntos = row[3] or 0

            logger.info(
                f"Linha: '{disc_descricao}' -> Módulos: {total_modulos}, Assuntos: {total_assuntos}"
            )

            # Aplica mapeamento de sinônimos (case-insensitive)
            disc_lower = disc_descricao.lower()
            disc_nome_final = nome_sinonimos.get(disc_lower, disc_descricao)

            logger.info(f"  Mapeado: '{disc_descricao}' -> '{disc_nome_final}'")

            # Acumula Literatura, Redação e Língua Portuguesa
            if disc_descricao in ["Literatura", "Redação", "Língua Portuguesa"]:
                lingua_port_modulos += total_modulos
                lingua_port_assuntos += total_assuntos
                logger.info(
                    f"  Acumulado LP: módulos={lingua_port_modulos}, assuntos={lingua_port_assuntos}"
                )
            else:
                # Armazena SEPARADAMENTE módulos e assuntos
                modulos_por_disc[disc_nome_final] = total_modulos
                assuntos_por_disc[disc_nome_final] = total_assuntos
                logger.info(
                    f"  Armazenado: ['{disc_nome_final}'] mod={total_modulos}, ass={total_assuntos}"
                )

        # Segundo passo: atribui os valores consolidados para Língua Portuguesa
        modulos_por_disc["Língua Portuguesa"] = lingua_port_modulos
        assuntos_por_disc["Língua Portuguesa"] = lingua_port_assuntos

        logger.info(f"FINAL - Módulos por disciplina: {modulos_por_disc}")
        logger.info(f"FINAL - Assuntos por disciplina: {assuntos_por_disc}")

    except Exception as e:
        logger.error(f"Erro ao contar módulos/assuntos: {e}", exc_info=True)
        modulos_por_disc = {}
        assuntos_por_disc = {}

    # Contagem de habilidades únicas por disciplina (from questoes com habilidade_id)
    try:
        # Mapeamento de nomes de disciplinas do trieduc
        nome_sinonimos_trieduc = {
            "Inglês": "Língua Inglesa",
            "Espanhol": "Língua Espanhola",
            "Arte": "Artes",
        }

        hab_rows = (
            db.query(
                QuestaoModel.disciplina_id,
                func.count(func.distinct(QuestaoModel.habilidade_id)),
            )
            .filter(QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None))
            .group_by(QuestaoModel.disciplina_id)
            .all()
        )

        habs_por_disc = {}
        lingua_port_habs = 0

        for d_id, count in hab_rows:
            if d_id in disc_names:
                nome_original = disc_names[d_id]
                # Aplica mapeamento de sinônimos
                nome_final = nome_sinonimos_trieduc.get(nome_original, nome_original)

                # Acumula Literatura, Redação e Língua Portuguesa
                if nome_original in ["Literatura", "Redação", "Língua Portuguesa"]:
                    lingua_port_habs += count
                else:
                    habs_por_disc[nome_final] = count

        # Atribui o valor consolidado para Língua Portuguesa
        habs_por_disc["Língua Portuguesa"] = lingua_port_habs

        logger.info(f"Habilidades por disciplina: {habs_por_disc}")
    except Exception as e:
        logger.warning(f"Erro ao contar habilidades: {e}")
        habs_por_disc = {}

    por_disciplina = {}

    # Mapeamento para padronizar nomes entre bancos
    nome_padrao_map = {
        "Inglês": "Língua Inglesa",
        "Espanhol": "Língua Espanhola",
        "Arte": "Artes",
    }

    for d_id, total_mysql in mysql_counts.items():
        if d_id is None:
            continue
        nome_original = disc_names.get(d_id, f"ID {d_id}")

        # Aplica mapeamento de padronização
        nome = nome_padrao_map.get(nome_original, nome_original)

        d_set = disc_ids_map.get(d_id, set())
        d_quatro_alt = len(quatro_alt_ids & d_set)
        total_disc = max(0, total_mysql - d_quatro_alt)
        d_manuais = len(manuais_ids & d_set)
        d_confirmacoes = len(confirmacoes_ids & d_set)
        d_alta_sim = len(alta_sim_ids & d_set)
        d_verificar = len(verificar_ids & d_set)
        d_puladas_trieduc = len(puladas_ids_disjoint & d_set)
        d_pendentes_trieduc = max(
            0,
            total_disc
            - d_manuais
            - d_confirmacoes
            - d_alta_sim
            - d_verificar
            - d_puladas_trieduc,
        )

        # Contribuição da base Superprofessor para essa disciplina
        d_sp_total = sp_por_disc_total.get(nome, 0)
        d_sp_classif = sp_por_disc_classif.get(nome, 0)
        d_sp_pulada = sp_por_disc_pulada.get(nome, 0)
        d_sp_pendentes = max(0, d_sp_total - d_sp_classif - d_sp_pulada)

        # Totais GLOBAIS (trieduc + SP)
        g_total = total_disc + d_sp_total
        g_classificadas = d_manuais + d_sp_classif
        g_pendentes = d_pendentes_trieduc + d_sp_pendentes
        g_puladas = d_puladas_trieduc + d_sp_pulada
        g_faltam = max(0, g_total - g_classificadas)

        por_disciplina[nome] = {
            # globais (trieduc + SP)
            "total": g_total,
            "feitas": g_classificadas,
            "faltam": g_faltam,
            "manuais": g_classificadas,
            "alta_sim": d_alta_sim,  # SP não tem alta_sim
            "auto": d_alta_sim,  # retrocompat
            "confirmacoes": d_confirmacoes,  # SP não tem
            "verificar": d_verificar,  # SP não tem
            "pendentes": g_pendentes,
            "puladas": g_puladas,
            # breakdown por base (tooltip)
            "trieduc_total": total_disc,
            "trieduc_classificadas": d_manuais,
            "trieduc_pendentes": d_pendentes_trieduc,
            "trieduc_puladas": d_puladas_trieduc,
            "sp_total": d_sp_total,
            "sp_classificadas": d_sp_classif,
            "sp_pendentes": d_sp_pendentes,
            "sp_puladas": d_sp_pulada,
            "total_modulos": modulos_por_disc.get(nome, 0),
            "total_habilidades": habs_por_disc.get(nome, 0),
            "total_assuntos": assuntos_por_disc.get(nome, 0),
        }

    # Adiciona disciplinas que existem só na base SP (não tem questão trieduc)
    for nome_sp, sp_total in sp_por_disc_total.items():
        if nome_sp in por_disciplina or nome_sp == "—":
            continue
        sp_classif = sp_por_disc_classif.get(nome_sp, 0)
        sp_pulada = sp_por_disc_pulada.get(nome_sp, 0)
        sp_pend = max(0, sp_total - sp_classif - sp_pulada)
        por_disciplina[nome_sp] = {
            "total": sp_total,
            "feitas": sp_classif,
            "faltam": max(0, sp_total - sp_classif),
            "manuais": sp_classif,
            "alta_sim": 0,
            "auto": 0,
            "confirmacoes": 0,
            "verificar": 0,
            "pendentes": sp_pend,
            "puladas": sp_pulada,
            "trieduc_total": 0,
            "trieduc_classificadas": 0,
            "trieduc_pendentes": 0,
            "trieduc_puladas": 0,
            "sp_total": sp_total,
            "sp_classificadas": sp_classif,
            "sp_pendentes": sp_pend,
            "sp_puladas": sp_pulada,
            "total_modulos": 0,
            "total_habilidades": 0,
            "total_assuntos": 0,
        }

    # Por usuário (Atividades Recentes)
    por_usuario_rows = (
        pg_db.query(
            UsuarioModel.nome,
            func.count(ClassificacaoUsuarioModel.id),
        )
        .join(UsuarioModel, UsuarioModel.id == ClassificacaoUsuarioModel.usuario_id)
        .group_by(UsuarioModel.nome)
        .all()
    )
    por_usuario = {row[0]: row[1] for row in por_usuario_rows}

    # ================================================================
    # TOTAIS GLOBAIS (trieduc + Superprofessor)
    # ================================================================
    total_sistema_global = total_sistema + total_superprofessor
    total_manuais_global = total_manuais + total_superprofessor_classificadas
    total_pendentes_global = total_pendentes + total_superprofessor_pendentes
    total_puladas_global = total_puladas + total_superprofessor_puladas

    res = ClassificacaoStatsResponse(
        total_classificacoes=total_sistema_global,
        classificacoes_novas=novas,
        confirmacoes=confirmacoes,
        correcoes=correcoes,
        usuarios_ativos=usuarios_ativos,
        total_manuais=total_manuais_global,
        total_pendentes=total_pendentes_global,
        total_sistema=total_sistema_global,
        total_precisa_verificar=total_precisa_verificar,
        total_auto_superpro=total_auto_superpro,
        total_alta_similaridade=total_alta_similaridade,
        total_confirmacoes_pendentes=total_confirmacoes_pendentes,
        total_4_alternativas=total_4_alternativas,
        total_puladas=total_puladas_global,
        # Breakdown trieduc
        total_trieduc=total_sistema,
        total_trieduc_classificadas=total_manuais,
        total_trieduc_pendentes=total_pendentes,
        total_trieduc_puladas=total_puladas,
        # Breakdown SP
        total_superprofessor=total_superprofessor,
        total_superprofessor_classificadas=total_superprofessor_classificadas,
        total_superprofessor_pendentes=total_superprofessor_pendentes,
        total_superprofessor_puladas=total_superprofessor_puladas,
        por_disciplina=por_disciplina,
        por_usuario=por_usuario,
    )
    return res


# ========================
# HISTÓRICO (para ML)
# ========================


@router.get(
    "/historico",
    response_model=HistoricoListResponse,
    summary="📋 Histórico de classificações (dados para ML)",
)
async def historico_classificacoes(
    page: int = Query(1, ge=1, description="Página"),
    per_page: int = Query(50, ge=1, le=200, description="Itens por página"),
    tipo_acao: Optional[str] = Query(None, description="Filtrar por tipo de ação"),
    usuario_id: Optional[int] = Query(None, description="Filtrar por usuário"),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna histórico paginado de todas as classificações feitas por usuários.
    Usado para exportação de dados de treino ML.
    """
    query = pg_db.query(ClassificacaoUsuarioModel)

    if tipo_acao:
        query = query.filter(ClassificacaoUsuarioModel.tipo_acao == tipo_acao)
    if usuario_id:
        query = query.filter(ClassificacaoUsuarioModel.usuario_id == usuario_id)

    total = query.count()
    pages = ceil(total / per_page) if total > 0 else 1
    offset = (page - 1) * per_page

    registros = (
        query.order_by(ClassificacaoUsuarioModel.id)
        .offset(offset)
        .limit(per_page)
        .all()
    )

    # Buscar nomes dos usuários
    usuario_ids = {r.usuario_id for r in registros}
    if usuario_ids:
        users = pg_db.query(UsuarioModel).filter(UsuarioModel.id.in_(usuario_ids)).all()
        user_map = {u.id: u.nome for u in users}
    else:
        user_map = {}

    for r in registros:
        data.append(
            ClassificacaoHistoricoSchema(
                id=r.id,
                usuario_id=r.usuario_id,
                usuario_nome=user_map.get(r.usuario_id),
                questao_id=r.questao_id,
                habilidade_id=r.habilidade_id,
                modulo_escolhido=r.modulo_escolhido,
                classificacao_trieduc=r.classificacao_trieduc,
                classificacao_extracao=r.classificacao_extracao,
                tipo_acao=r.tipo_acao,
                observacao=r.observacao,
                created_at=r.created_at,
            )
        )

    return HistoricoListResponse(
        data=data,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ========================
# SUPERPROFESSOR
# ========================


@router.get(
    "/superprofessor/disciplinas",
    summary="Disciplinas SP disponiveis",
)
async def listar_disciplinas_superprofessor(
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de disciplinas do superprofessor com contagem de questões pendentes (não classificadas).
    """
    classificados_sp_ids = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_superprofessor", "pular_superprofessor"]
            )
        )
        .distinct()
        .all()
    }

    query = pg_db.query(
        QuestaoSuperprofessorModel.disciplina_sp,
        func.count(QuestaoSuperprofessorModel.sp_id),
    ).filter(QuestaoSuperprofessorModel.disciplina_sp.isnot(None))

    if classificados_sp_ids:
        query = query.filter(
            ~QuestaoSuperprofessorModel.sp_id.in_(list(classificados_sp_ids))
        )

    rows = (
        query.group_by(QuestaoSuperprofessorModel.disciplina_sp)
        .order_by(QuestaoSuperprofessorModel.disciplina_sp)
        .all()
    )
    return {"disciplinas": [{"nome": r[0], "total": r[1]} for r in rows if r[0]]}


@router.get(
    "/superprofessor/assuntos",
    summary="Assuntos SP disponiveis",
)
async def listar_assuntos_superprofessor(
    disciplina: Optional[str] = Query(None, description="Filtrar por disciplina SP"),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de assuntos (assunto_sp) do superprofessor com contagem de questões pendentes (não classificadas).
    """
    classificados_sp_ids = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_superprofessor", "pular_superprofessor"]
            )
        )
        .distinct()
        .all()
    }

    query = pg_db.query(
        QuestaoSuperprofessorModel.assunto_sp,
        func.count(QuestaoSuperprofessorModel.sp_id),
    ).filter(
        QuestaoSuperprofessorModel.assunto_sp.isnot(None),
        QuestaoSuperprofessorModel.assunto_sp != "",
    )

    if classificados_sp_ids:
        query = query.filter(
            ~QuestaoSuperprofessorModel.sp_id.in_(list(classificados_sp_ids))
        )

    if disciplina:
        query = query.filter(QuestaoSuperprofessorModel.disciplina_sp == disciplina)
    rows = (
        query.group_by(QuestaoSuperprofessorModel.assunto_sp)
        .order_by(QuestaoSuperprofessorModel.assunto_sp)
        .all()
    )
    return {"assuntos": [{"nome": r[0], "total": r[1]} for r in rows if r[0]]}


@router.get(
    "/superprofessor/stats",
    response_model=SuperprofessorStatsResponse,
    summary="Estatisticas do superprofessor",
)
async def stats_superprofessor(
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna estatísticas do módulo superprofessor.
    """
    total_questoes = pg_db.query(QuestaoSuperprofessorModel).count()

    classificadas_ids = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_superprofessor")
        .distinct()
        .all()
    }
    total_classificadas = len(classificadas_ids)

    puladas_ids = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "pular_superprofessor")
        .distinct()
        .all()
    } - classificadas_ids
    total_puladas = len(puladas_ids)

    total_pendentes = max(0, total_questoes - total_classificadas - total_puladas)

    # Por disciplina SP
    disc_rows = (
        pg_db.query(
            QuestaoSuperprofessorModel.disciplina_sp,
            func.count(QuestaoSuperprofessorModel.sp_id),
        )
        .filter(QuestaoSuperprofessorModel.disciplina_sp.isnot(None))
        .group_by(QuestaoSuperprofessorModel.disciplina_sp)
        .all()
    )

    # Mapa sp_id → disciplina_sp para contagens
    sp_disc_rows = pg_db.query(
        QuestaoSuperprofessorModel.sp_id,
        QuestaoSuperprofessorModel.disciplina_sp,
    ).all()
    sp_disc_map: dict[int, str] = {r[0]: r[1] for r in sp_disc_rows if r[1]}

    por_disciplina = {}
    for disc_name, total in disc_rows:
        if not disc_name:
            continue
        sp_ids_disc = {sp_id for sp_id, d in sp_disc_map.items() if d == disc_name}
        classif_disc = len(classificadas_ids & sp_ids_disc)
        puladas_disc = len(puladas_ids & sp_ids_disc)
        pend_disc = max(0, total - classif_disc - puladas_disc)
        por_disciplina[disc_name] = {
            "total": total,
            "classificadas": classif_disc,
            "puladas": puladas_disc,
            "pendentes": pend_disc,
        }

    # Por usuário
    user_rows = (
        pg_db.query(
            UsuarioModel.nome,
            func.count(ClassificacaoUsuarioModel.id),
        )
        .join(UsuarioModel, UsuarioModel.id == ClassificacaoUsuarioModel.usuario_id)
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_superprofessor", "pular_superprofessor"]
            )
        )
        .group_by(UsuarioModel.nome)
        .all()
    )
    por_usuario = {row[0]: row[1] for row in user_rows}

    return SuperprofessorStatsResponse(
        total_questoes=total_questoes,
        total_classificadas=total_classificadas,
        total_puladas=total_puladas,
        total_pendentes=total_pendentes,
        por_disciplina=por_disciplina,
        por_usuario=por_usuario,
    )


@router.get(
    "/superprofessor/cobertura-assuntos",
    summary="📊 Cobertura de questões por assunto (CSV mapeamento)",
)
async def cobertura_assuntos_superpro(
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna, para cada (disc_modu_id, assu_id) do CSV mapeamento_modulos_assuntos.csv,
    quantas classificações superprofessor existem para aquele assunto.

    Útil para identificar assuntos sem cobertura (gaps) na base SP.
    """
    from sqlalchemy import text as sql_text

    cache_key = "cobertura_assuntos_sp_v1"
    cached = get_from_cache(cache_key, ttl=300)
    if cached is not None:
        return cached

    pairs: list[tuple[int, int]] = list(_MAPEAMENTO_SP_PAIRS)

    if not pairs:
        return {
            "totais": {
                "total_assuntos_csv": 0,
                "assuntos_com_questoes": 0,
                "assuntos_sem_questoes": 0,
            },
            "items": [],
        }

    modu_ids = list({p[0] for p in pairs})
    assu_ids = list({p[1] for p in pairs})

    # Lookup descricoes (módulos + disciplinas + assuntos)
    modu_info: dict[int, dict] = {}
    BATCH = 1000
    for i in range(0, len(modu_ids), BATCH):
        chunk = modu_ids[i : i + BATCH]
        ids_str = ",".join(str(x) for x in chunk)
        rows = db.execute(sql_text(f"""
            SELECT dm.disc_modu_id, dm.disc_modu_descricao, d.disc_descricao
            FROM compartilhados.disciplinas_modulos dm
            JOIN compartilhados.disciplinas d ON d.disc_id = dm.disc_id
            WHERE dm.disc_modu_id IN ({ids_str})
        """)).fetchall()
        for r in rows:
            modu_info[r[0]] = {"modulo": r[1] or "?", "disciplina": r[2] or "?"}

    assu_info: dict[int, str] = {}
    for i in range(0, len(assu_ids), BATCH):
        chunk = assu_ids[i : i + BATCH]
        ids_str = ",".join(str(x) for x in chunk)
        rows = db.execute(sql_text(f"""
            SELECT assu_id, assu_descricao
            FROM compartilhados.assuntos
            WHERE assu_id IN ({ids_str})
        """)).fetchall()
        for r in rows:
            assu_info[r[0]] = r[1] or f"assu_id={r[0]}"

    # Pré-computar contagem de classificações SP por (módulo, assunto) descrição.
    # Cada classificação tem listas paralelas modulos_escolhidos[] e descricoes_assunto_list[];
    # contamos pares (modulo, assunto) em todas as classificações SP.
    sp_classifs = (
        pg_db.query(
            ClassificacaoUsuarioModel.descricao_assunto,
            ClassificacaoUsuarioModel.descricoes_assunto_list,
            ClassificacaoUsuarioModel.modulo_escolhido,
            ClassificacaoUsuarioModel.modulos_escolhidos,
        )
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_superprofessor")
        .all()
    )

    # Conta por par (modulo_desc, assunto_desc) — case-insensitive trim
    contagem_pares: dict[tuple[str, str], int] = {}
    contagem_assunto_so: dict[str, int] = {}
    for desc_a, desc_list, modulo, modulos in sp_classifs:
        pares_locais: set[tuple[str, str]] = set()
        if desc_list and modulos and len(desc_list) == len(modulos):
            for m, a in zip(modulos, desc_list):
                if a and m:
                    pares_locais.add((str(m).strip().lower(), str(a).strip().lower()))
        elif desc_a and modulo:
            pares_locais.add((str(modulo).strip().lower(), str(desc_a).strip().lower()))
        # Também contagem só por assunto (fallback caso modulo não bata)
        assuntos_locais = set()
        if desc_list:
            for a in desc_list:
                if a:
                    assuntos_locais.add(str(a).strip().lower())
        elif desc_a:
            assuntos_locais.add(str(desc_a).strip().lower())

        for par in pares_locais:
            contagem_pares[par] = contagem_pares.get(par, 0) + 1
        for a in assuntos_locais:
            contagem_assunto_so[a] = contagem_assunto_so.get(a, 0) + 1

    items = []
    com_questoes = 0
    for disc_modu_id, assu_id in pairs:
        modu = modu_info.get(disc_modu_id, {"modulo": "?", "disciplina": "?"})
        assunto_desc = assu_info.get(assu_id, f"assu_id={assu_id}")
        key_par = (
            str(modu["modulo"]).strip().lower(),
            str(assunto_desc).strip().lower(),
        )
        # Tentativa 1: contagem por par (módulo, assunto)
        n_par = contagem_pares.get(key_par, 0)
        # Tentativa 2 (fallback): contagem só por assunto
        n_assunto = contagem_assunto_so.get(key_par[1], 0)
        n_final = n_par if n_par > 0 else n_assunto
        if n_final > 0:
            com_questoes += 1
        items.append(
            {
                "disciplina": modu["disciplina"],
                "disc_modu_id": disc_modu_id,
                "modulo": modu["modulo"],
                "assu_id": assu_id,
                "assunto": assunto_desc,
                "questoes_classificadas": n_final,
                "matched_by": (
                    "par" if n_par > 0 else ("assunto" if n_assunto > 0 else "nenhum")
                ),
            }
        )

    # Ordena por disciplina → módulo → quantidade (asc para gaps no topo) → assunto
    items.sort(
        key=lambda x: (
            x["disciplina"],
            x["modulo"],
            x["questoes_classificadas"],
            x["assunto"],
        )
    )

    result = {
        "totais": {
            "total_assuntos_csv": len(pairs),
            "assuntos_com_questoes": com_questoes,
            "assuntos_sem_questoes": len(pairs) - com_questoes,
            "modulos_unicos": len(modu_ids),
        },
        "items": items,
    }
    set_to_cache(cache_key, result)
    return result


@router.get(
    "/superprofessor/cobertura-libro",
    summary="📊 Cobertura de questões por assunto Libro (total geral + SP)",
)
async def cobertura_assuntos_libro(
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna todos os pares (módulo, assunto) do banco compartilhados (excluindo [RM]).

    Para cada par retorna:
    - total_classificacoes: contagem de TODAS as classificações (qualquer tipo_acao) para aquele assunto
    - total_sp: contagem apenas das classificações_superprofessor
    - no_csv: se o par estava no mapeamento_modulos_assuntos.csv (targetado por ter poucas questões)

    O filtro "apenas mapeados SP" no frontend usa no_csv=True para mostrar só os pares
    que foram trabalhados — revelando o volume real mesmo somando todos os tipos.
    """
    from sqlalchemy import text as sql_text

    cache_key = "cobertura_assuntos_libro_v3"
    cached = get_from_cache(cache_key, ttl=300)
    if cached is not None:
        return cached

    # 1. Pares mapeados para busca SP (embutidos como constante)
    csv_pairs: frozenset = _MAPEAMENTO_SP_PAIRS

    # 2. Buscar todos os pares (módulo, assunto) do banco compartilhados
    rows = db.execute(sql_text("""
        SELECT
            d.disc_id,
            d.disc_descricao AS disciplina,
            dm.disc_modu_id,
            dm.disc_modu_descricao AS modulo,
            a.assu_id,
            a.assu_descricao AS assunto
        FROM compartilhados.disciplinas_modulos dm
        JOIN compartilhados.disciplinas d ON d.disc_id = dm.disc_id
        JOIN compartilhados.assuntos a ON a.disc_modu_id = dm.disc_modu_id
        WHERE dm.disc_modu_descricao NOT LIKE '[RM]%'
          AND a.assu_descricao NOT LIKE '[RM]%'
        ORDER BY d.disc_descricao, dm.disc_modu_descricao, a.assu_descricao
    """)).fetchall()

    # 3. Pré-computar contagem de classificações por (modulo_desc, assunto_desc)
    #    — separado: total geral e apenas SP
    def _build_contagens(tipo_acao_filter=None):
        q = pg_db.query(
            ClassificacaoUsuarioModel.descricao_assunto,
            ClassificacaoUsuarioModel.descricoes_assunto_list,
            ClassificacaoUsuarioModel.modulo_escolhido,
            ClassificacaoUsuarioModel.modulos_escolhidos,
        )
        if tipo_acao_filter:
            q = q.filter(ClassificacaoUsuarioModel.tipo_acao == tipo_acao_filter)
        classifs = q.all()

        pares: dict[tuple[str, str], int] = {}
        assunto_so: dict[str, int] = {}
        for desc_a, desc_list, modulo, modulos in classifs:
            pares_locais: set[tuple[str, str]] = set()
            if desc_list and modulos and len(desc_list) == len(modulos):
                for m, a in zip(modulos, desc_list):
                    if a and m:
                        pares_locais.add(
                            (str(m).strip().lower(), str(a).strip().lower())
                        )
            elif desc_a and modulo:
                pares_locais.add(
                    (str(modulo).strip().lower(), str(desc_a).strip().lower())
                )
            assuntos_locais: set[str] = set()
            if desc_list:
                for a in desc_list:
                    if a:
                        assuntos_locais.add(str(a).strip().lower())
            elif desc_a:
                assuntos_locais.add(str(desc_a).strip().lower())
            for par in pares_locais:
                pares[par] = pares.get(par, 0) + 1
            for a in assuntos_locais:
                assunto_so[a] = assunto_so.get(a, 0) + 1
        return pares, assunto_so

    pares_total, assunto_total = _build_contagens(tipo_acao_filter=None)
    pares_sp, assunto_sp = _build_contagens(
        tipo_acao_filter="classificacao_superprofessor"
    )

    def _lookup(pares, assunto_so, key_par):
        n = pares.get(key_par, 0)
        if n == 0:
            n = assunto_so.get(key_par[1], 0)
        return n

    # 4. Montar items
    items = []
    for row in rows:
        disc_id, disciplina, disc_modu_id, modulo, assu_id, assunto = row
        key_par = (str(modulo).strip().lower(), str(assunto).strip().lower())
        n_total = _lookup(pares_total, assunto_total, key_par)
        n_sp = _lookup(pares_sp, assunto_sp, key_par)
        no_csv = (disc_modu_id, assu_id) in csv_pairs
        items.append(
            {
                "disc_id": disc_id,
                "disciplina": disciplina,
                "disc_modu_id": disc_modu_id,
                "modulo": modulo,
                "assu_id": assu_id,
                "assunto": assunto,
                "total_classificacoes": n_total,
                "total_sp": n_sp,
                "no_csv": no_csv,
            }
        )

    csv_items = [i for i in items if i["no_csv"]]
    result = {
        "totais": {
            "total_assuntos": len(csv_items),
            "com_classificacoes_sp": sum(1 for i in csv_items if i["total_sp"] > 0),
            "sem_classificacoes_sp": sum(1 for i in csv_items if i["total_sp"] == 0),
            "abaixo_5_sp": sum(1 for i in csv_items if 0 < i["total_sp"] < 5),
        },
        "items": csv_items,
    }
    set_to_cache(cache_key, result)
    return result


@router.get(
    "/superprofessor/proxima",
    response_model=QuestaoSuperprofessorResponse,
    summary="Proxima questao superprofessor",
)
async def proxima_questao_superprofessor(
    disciplina: Optional[str] = Query(None, description="Filtrar por disciplina SP"),
    assunto_sp: Optional[str] = Query(None, description="Filtrar por assunto SP"),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão do superprofessor que ainda não foi revisada pelo usuário.
    Mostra a classificação SP original e o mapeamento libro já feito.
    """
    # sp_ids já classificados por QUALQUER usuário (progresso compartilhado)
    classificados_sp_ids: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_superprofessor", "pular_superprofessor"]
            ),
        )
        .distinct()
        .all()
    }

    query = pg_db.query(QuestaoSuperprofessorModel)

    if disciplina:
        query = query.filter(QuestaoSuperprofessorModel.disciplina_sp == disciplina)

    if assunto_sp:
        query = query.filter(QuestaoSuperprofessorModel.assunto_sp == assunto_sp)

    if classificados_sp_ids:
        query = query.filter(
            ~QuestaoSuperprofessorModel.sp_id.in_(list(classificados_sp_ids))
        )

    total_pendentes = query.count()
    questao = query.order_by(QuestaoSuperprofessorModel.sp_id.asc()).first()

    if not questao:
        raise HTTPException(
            status_code=404, detail="Nenhuma questão pendente encontrada"
        )

    # Buscar módulos e assuntos de compartilhados, filtrados pelas disciplinas_libro
    modulos_possiveis = []
    disciplinas_libro = questao.disciplinas_libro or []

    if disciplinas_libro:
        # Expandir disciplinas que possuem aliases no banco compartilhados
        _aliases_disciplinas = {
            "Língua Portuguesa": ["Literatura", "Redação"],
            "Artes": ["Arte"],
            "Língua Inglesa": ["Inglês"],
        }
        disciplinas_expandidas = []
        for disc in disciplinas_libro:
            disciplinas_expandidas.append(disc)
            disciplinas_expandidas.extend(_aliases_disciplinas.get(disc, []))

        # Remover duplicatas e manter ordem
        disciplinas_expandidas = list(dict.fromkeys(disciplinas_expandidas))

        placeholders = ", ".join(
            f":{f'disc{i}'}" for i in range(len(disciplinas_expandidas))
        )
        params = {f"disc{i}": v for i, v in enumerate(disciplinas_expandidas)}
        sql = sql_text(f"""
            SELECT
                a.assu_id          AS id,
                d.disc_descricao   AS disciplina,
                dm.disc_modu_descricao AS modulo,
                a.assu_descricao   AS descricao
            FROM compartilhados.disciplinas d
            JOIN compartilhados.disciplinas_modulos dm
                ON dm.disc_id = d.disc_id
            JOIN compartilhados.assuntos a
                ON a.disc_modu_id = dm.disc_modu_id
            WHERE d.disc_descricao IN ({placeholders})
              AND TRIM(dm.disc_modu_descricao) NOT LIKE '[RM]%%'
              AND TRIM(a.assu_descricao) NOT LIKE '[RM]%%'
            ORDER BY d.disc_descricao, dm.disc_modu_descricao, a.assu_descricao
        """)
        rows = pg_db.execute(sql, params).fetchall()
        modulos_possiveis = [
            HabilidadeModuloSchema(
                id=row.id,
                habilidade_id=None,
                habilidade_descricao="",
                area="",
                disciplina=row.disciplina,
                modulo=row.modulo,
                descricao=row.descricao,
                ordenacao=None,
            )
            for row in rows
        ]

    # Buscar alternativas
    alternativas = []
    alt_rows = (
        pg_db.query(AlternativaSuperprofessorModel)
        .filter(AlternativaSuperprofessorModel.sp_id == questao.sp_id)
        .order_by(AlternativaSuperprofessorModel.letra)
        .all()
    )
    gabarito = (questao.gabarito or "").strip().upper()
    for alt in alt_rows:
        letra = (alt.letra or "").strip().upper()
        alternativas.append(
            AlternativaSuperprofessorSchema(
                letra=alt.letra,
                texto=alt.texto or "",
                correta=bool(gabarito and letra == gabarito),
            )
        )

    return QuestaoSuperprofessorResponse(
        id=questao.sp_id,
        sp_id=questao.sp_id,
        enunciado=questao.enunciado,
        disciplina_sp=questao.disciplina_sp,
        classif_sp_breadcrumb=questao.classif_sp_breadcrumb,
        assunto_sp=questao.assunto_sp,
        disciplinas_libro=disciplinas_libro,
        assuntos_libro=questao.assuntos_libro,
        alternativas=alternativas,
        gabarito=questao.gabarito,
        modulos_possiveis=modulos_possiveis,
        total_pendentes=total_pendentes,
    )


@router.post(
    "/superprofessor/salvar",
    response_model=SalvarClassificacaoResponse,
    summary="Salvar revisao superprofessor",
)
async def salvar_superprofessor(
    request: SalvarSuperprofessorRequest,
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Salva a revisão de uma questão superprofessor.
    Registra em classificacao_usuario com tipo_acao='classificacao_superprofessor'.
    O questao_id armazenado é o sp_id (ID original no banco superprofessor).
    """
    questao = (
        pg_db.query(QuestaoSuperprofessorModel)
        .filter(QuestaoSuperprofessorModel.sp_id == request.questao_nova_id)
        .first()
    )

    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    # Remover registros de pular desta questão (de qualquer usuário) antes de classificar
    pg_db.query(ClassificacaoUsuarioModel).filter(
        ClassificacaoUsuarioModel.questao_id == questao.sp_id,
        ClassificacaoUsuarioModel.tipo_acao == "pular_superprofessor",
    ).delete(synchronize_session=False)

    classificacao = ClassificacaoUsuarioModel(
        usuario_id=usuario.id,
        questao_id=questao.sp_id,
        habilidade_id=None,
        modulo_escolhido=request.modulo_escolhido,
        classificacao_trieduc=request.classificacao_trieduc,
        descricao_assunto=request.descricao_assunto,
        habilidade_modulo_id=request.habilidade_modulo_id,
        modulos_escolhidos=request.modulos_escolhidos,
        classificacoes_trieduc_list=request.classificacoes_trieduc,
        descricoes_assunto_list=request.descricoes_assunto,
        habilidade_modulo_ids=request.habilidade_modulo_ids,
        classificacao_extracao=None,
        tipo_acao="classificacao_superprofessor",
        observacao=request.observacao,
    )
    pg_db.add(classificacao)
    pg_db.commit()

    logger.info(
        f"Superprofessor salvo: usuario={usuario.nome}, sp_id={questao.sp_id}, "
        f"modulos={request.modulos_escolhidos or [request.modulo_escolhido]}"
    )

    return SalvarClassificacaoResponse(
        success=True,
        id=classificacao.id,
        questao_id=questao.sp_id,
        tipo_acao="classificacao_superprofessor",
        message="Classificação superprofessor salva com sucesso",
    )


@router.post(
    "/superprofessor/pular",
    summary="Pular questao superprofessor",
)
async def pular_superprofessor(
    request: PularSuperprofessorRequest,
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Pula uma questão superprofessor para o usuário atual.
    Registra em classificacao_usuario com tipo_acao='pular_superprofessor'.
    """
    questao = (
        pg_db.query(QuestaoSuperprofessorModel)
        .filter(QuestaoSuperprofessorModel.sp_id == request.questao_nova_id)
        .first()
    )

    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    pulo = ClassificacaoUsuarioModel(
        usuario_id=usuario.id,
        questao_id=questao.sp_id,
        habilidade_id=None,
        tipo_acao="pular_superprofessor",
    )
    pg_db.add(pulo)
    pg_db.commit()

    return {"success": True, "message": "Questão pulada"}


@router.get(
    "/superprofessor/pendentes",
    response_model=list[QuestaoSuperprofessorResponse],
    summary="Listar questoes superprofessor puladas",
)
async def listar_pendentes_superprofessor(
    disciplina: Optional[str] = Query(None, description="Filtrar por disciplina SP"),
    assunto_sp: Optional[str] = Query(None, description="Filtrar por assunto SP"),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna todas as questões superprofessor que foram puladas por este usuário.
    Estas são questões que o usuário escolheu "pular" e pode revisitar para classificar.
    """
    # Buscar sp_ids pulados por qualquer usuário
    pulados_sp_ids: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "pular_superprofessor")
        .distinct()
        .all()
    }

    if not pulados_sp_ids:
        return []

    # Excluir questões que já foram classificadas por qualquer usuário
    classificados_sp_ids: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_superprofessor")
        .distinct()
        .all()
    }
    pulados_sp_ids -= classificados_sp_ids

    if not pulados_sp_ids:
        return []

    query = pg_db.query(QuestaoSuperprofessorModel).filter(
        QuestaoSuperprofessorModel.sp_id.in_(list(pulados_sp_ids))
    )

    if disciplina:
        query = query.filter(QuestaoSuperprofessorModel.disciplina_sp == disciplina)

    if assunto_sp:
        query = query.filter(QuestaoSuperprofessorModel.assunto_sp == assunto_sp)

    questoes = query.order_by(QuestaoSuperprofessorModel.sp_id.asc()).all()

    resultado = []
    for questao in questoes:
        # Buscar módulos
        modulos_possiveis = []
        disciplinas_libro = questao.disciplinas_libro or []

        if disciplinas_libro:
            _aliases_disciplinas = {
                "Língua Portuguesa": ["Literatura", "Redação"],
                "Artes": ["Arte"],
                "Língua Inglesa": ["Inglês"],
            }
            disciplinas_expandidas = []
            for disc in disciplinas_libro:
                disciplinas_expandidas.append(disc)
                disciplinas_expandidas.extend(_aliases_disciplinas.get(disc, []))

            disciplinas_expandidas = list(dict.fromkeys(disciplinas_expandidas))

            placeholders = ", ".join(
                f":{f'disc{i}'}" for i in range(len(disciplinas_expandidas))
            )
            params = {f"disc{i}": v for i, v in enumerate(disciplinas_expandidas)}
            sql = sql_text(f"""
                SELECT
                    a.assu_id AS id,
                    d.disc_descricao AS disciplina,
                    dm.disc_modu_descricao AS modulo,
                    a.assu_descricao AS descricao
                FROM compartilhados.disciplinas d
                JOIN compartilhados.disciplinas_modulos dm ON dm.disc_id = d.disc_id
                JOIN compartilhados.assuntos a ON a.disc_modu_id = dm.disc_modu_id
                WHERE d.disc_descricao IN ({placeholders})
                  AND TRIM(dm.disc_modu_descricao) NOT LIKE '[RM]%%'
                  AND TRIM(a.assu_descricao) NOT LIKE '[RM]%%'
                ORDER BY d.disc_descricao, dm.disc_modu_descricao, a.assu_descricao
            """)
            rows = pg_db.execute(sql, params).fetchall()
            modulos_possiveis = [
                HabilidadeModuloSchema(
                    id=row.id,
                    habilidade_id=None,
                    habilidade_descricao="",
                    area="",
                    disciplina=row.disciplina,
                    modulo=row.modulo,
                    descricao=row.descricao,
                    ordenacao=None,
                )
                for row in rows
            ]

        # Buscar alternativas
        alternativas = []
        alt_rows = (
            pg_db.query(AlternativaSuperprofessorModel)
            .filter(AlternativaSuperprofessorModel.sp_id == questao.sp_id)
            .order_by(AlternativaSuperprofessorModel.letra)
            .all()
        )
        gabarito = (questao.gabarito or "").strip().upper()
        for alt in alt_rows:
            letra = (alt.letra or "").strip().upper()
            alternativas.append(
                AlternativaSuperprofessorSchema(
                    letra=alt.letra,
                    texto=alt.texto or "",
                    correta=bool(gabarito and letra == gabarito),
                )
            )

        resultado.append(
            QuestaoSuperprofessorResponse(
                id=questao.sp_id,
                sp_id=questao.sp_id,
                enunciado=questao.enunciado,
                disciplina_sp=questao.disciplina_sp,
                classif_sp_breadcrumb=questao.classif_sp_breadcrumb,
                assunto_sp=questao.assunto_sp,
                disciplinas_libro=disciplinas_libro,
                assuntos_libro=questao.assuntos_libro,
                alternativas=alternativas,
                gabarito=questao.gabarito,
                modulos_possiveis=modulos_possiveis,
                total_pendentes=len(questoes),
            )
        )

    return resultado


@router.post(
    "/superprofessor/limpar-duplicados",
    summary="Remove registros pular duplicados para questoes ja classificadas",
)
async def limpar_duplicados_superprofessor(
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Remove registros pular_superprofessor de questões que já possuem
    um registro classificacao_superprofessor. Mantém apenas a classificação.
    """
    classificados_sp_ids = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_superprofessor")
        .distinct()
        .all()
    }

    if not classificados_sp_ids:
        return {"removidos": 0, "mensagem": "Nenhum duplicado encontrado"}

    deletados = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(
            ClassificacaoUsuarioModel.questao_id.in_(list(classificados_sp_ids)),
            ClassificacaoUsuarioModel.tipo_acao == "pular_superprofessor",
        )
        .delete(synchronize_session=False)
    )
    pg_db.commit()

    return {
        "removidos": deletados,
        "mensagem": f"{deletados} registros de pular removidos",
    }
