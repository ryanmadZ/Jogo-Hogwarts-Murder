"""MURDER HOGWARTS - jogo completo em um arquivo só.
Rode:  python jogo.py     (precisa de:  pip install pygame)
- Quem abre primeiro vira o servidor automaticamente.
- Os amigos abrem o mesmo arquivo e digitam o IP do anfitrião no campo "Servidor".
- Jogando sozinho para testar: abra 4 janelas e use 127.0.0.1.
"""
import pygame, socket, threading, json, math, sys, time, random, string
from collections import Counter

HOST, PORT = "0.0.0.0", 8765
MIN_J, MAX_J = 4, 12
LOCK = threading.RLock()
SALAS = {}
NOMES = {"killer": "Assassino", "sheriff": "Xerife", "angel": "Anjo", "citizen": "Cidadão"}


def raw(conn, d):
    try:
        conn.sendall((json.dumps(d, ensure_ascii=False) + "\n").encode())
        return True
    except OSError:
        return False


class Sala:
    def __init__(s, cod):
        s.cod, s.players, s.nid, s.host = cod, {}, 1, None
        s.cv = threading.Condition(LOCK)
        s.resumo = ""
        s.reset()

    def reset(s):
        s.phase, s.step, s.msg = "lobby", "", "Aguardando jogadores..."
        s.deadline = time.time()
        s.acts, s.votes, s.rodando = {}, {}, False
        for p in s.players.values():
            p["role"], p["alive"] = None, True

    def vivos(s):
        return [p for p in s.players.values() if p["alive"]]

    def enviar(s, p, d):
        if p["conn"] and not raw(p["conn"], d):
            p["conn"] = None

    def estado(s, p):
        fim = s.phase == "end"
        lista = [{"id": q["id"], "nick": q["nick"], "character": q["character"],
                  "alive": q["alive"],
                  "role": q["role"] if (not q["alive"] or fim) else None}
                 for q in s.players.values()]
        if s.phase == "night" and s.step == p["role"]:
            agi = p["role"] in s.acts
        else:
            agi = s.phase == "vote" and p["id"] in s.votes
        return {"type": "state", "room": s.cod, "players": lista, "my_id": p["id"],
                "my_role": p["role"] or "", "alive": p["alive"], "phase": s.phase,
                "step": s.step, "message": s.msg, "host": s.host,
                "time_left": max(0, int(s.deadline - time.time())),
                "voted": list(s.votes) if s.phase == "vote" else [], "acted": agi}

    def todos(s):
        for p in list(s.players.values()):
            s.enviar(p, s.estado(p))

    def espera(s, seg, pred=lambda: False):
        s.deadline = time.time() + seg
        s.todos()
        while s.rodando and not pred():
            r = s.deadline - time.time()
            if r <= 0:
                break
            s.cv.wait(r)

    def fim(s):
        v = s.vivos()
        k = [p for p in v if p["role"] == "killer"]
        pre = (s.resumo + " ") if s.resumo else ""
        if not k:
            s.msg = pre + "Os cidadãos venceram! O assassino foi derrotado."
            return True
        if len(k) >= len(v) - len(k):
            s.msg = pre + "O Assassino venceu! Hogwarts caiu nas sombras."
            return True
        return False

    def noite(s):
        s.acts, s.votes = {}, {}
        s.phase, s.step, s.msg = "night", "sleep", "A cidade dorme..."
        s.espera(5)
        for papel in ("killer", "sheriff", "angel"):
            nome = NOMES[papel]
            s.step, s.msg = papel, f"{nome}, acorde..."
            if any(p["role"] == papel and p["alive"] for p in s.players.values()):
                s.espera(30, lambda: papel in s.acts)
            else:  # papel morto: finge que está acordado para não revelar nada
                s.espera(random.uniform(7, 12))
            s.step, s.msg = "sleep", f"{nome}, durma..."
            s.espera(3)
        k, sh, an = s.acts.get("killer"), s.acts.get("sheriff"), s.acts.get("angel")
        mortos = set()
        if k and k != an:
            mortos.add(k)
        if sh and sh != an:
            mortos.add(sh)
            if s.players[sh]["role"] != "killer":  # xerife atirou num inocente
                mortos |= {p["id"] for p in s.players.values() if p["role"] == "sheriff"}
        nomes = []
        for i in mortos:
            p = s.players[i]
            if p["alive"]:
                p["alive"] = False
                nomes.append(f"{p['nick']} ({p['character']})")
        s.resumo = ("Ao amanhecer, encontraram sem vida: " + ", ".join(nomes) + "."
                    if nomes else "Todos acordaram sãos e salvos.")

    def dia(s):
        s.phase, s.step = "day", ""
        s.msg = s.resumo + " Debatam quem pode ser o assassino!"
        s.espera(30)
        s.phase, s.votes = "vote", {}
        s.msg = "Votação! Escolha quem será expulso da escola."
        s.espera(45, lambda: all(p["id"] in s.votes for p in s.vivos()))
        c = Counter(v for v in s.votes.values() if v)
        top = c.most_common(2)
        s.phase = "vote_result"
        if top and (len(top) == 1 or top[0][1] > top[1][1]):
            p = s.players[top[0][0]]
            p["alive"] = False
            s.msg = f"{p['nick']} ({p['character']}) foi expulso... Era {NOMES[p['role']]}!"
        else:
            s.msg = "Empate ou votos pulados: ninguém foi expulso."
        s.resumo = s.msg
        s.espera(7)

    def jogo(s):
        with s.cv:
            ids = list(s.players)
            random.shuffle(ids)
            for i, pid in enumerate(ids):
                s.players[pid]["role"] = ("killer", "sheriff", "angel")[i] if i < 3 else "citizen"
            s.phase, s.step, s.msg = "roles", "", "Descubra seu papel em segredo..."
            s.espera(8)
            while s.rodando:
                s.noite()
                if not s.rodando or s.fim():
                    break
                s.dia()
                if not s.rodando or s.fim():
                    break
            if s.rodando:
                s.phase, s.step = "end", ""
                s.espera(12)
            if s.rodando:
                for pid in [i for i, q in s.players.items() if not q["conn"]]:
                    del s.players[pid]
                s.reset()
                s.todos()


def tratar(conn, m, p, s):
    t = m.get("type")
    if t == "join" and not p:
        nick = str(m.get("nick", "")).strip()[:18]
        cod = str(m.get("room", "")).strip().upper()[:8]
        per = str(m.get("character", ""))
        if not nick:
            return raw(conn, {"type": "error", "message": "Nick inválido."}) and None
        while not cod:
            c = "".join(random.choices(string.ascii_uppercase, k=4))
            cod = c if c not in SALAS else ""
        s = SALAS.get(cod) or Sala(cod)
        erro = None
        if s.phase != "lobby":
            erro = "A partida dessa sala já começou."
        elif len(s.players) >= MAX_J:
            erro = "Sala cheia."
        elif any(q["nick"].lower() == nick.lower() for q in s.players.values()):
            erro = "Esse nick já está na sala."
        elif any(q["character"] == per for q in s.players.values()):
            erro = "Esse personagem já foi escolhido. Troque de personagem."
        if erro:
            raw(conn, {"type": "error", "message": erro})
            return None
        SALAS[cod] = s
        p = {"id": s.nid, "nick": nick, "character": per, "alive": True,
             "role": None, "conn": conn}
        s.nid += 1
        s.players[p["id"]] = p
        if s.host is None:
            s.host = p["id"]
        s.todos()
        return p, s
    if not p:
        return None
    if t == "start" and p["id"] == s.host and s.phase == "lobby":
        if len(s.players) < MIN_J:
            s.enviar(p, {"type": "error", "message": f"Mínimo de {MIN_J} jogadores."})
        else:
            s.rodando = True
            threading.Thread(target=s.jogo, daemon=True).start()
    elif t == "night_action" and s.phase == "night" and p["alive"] \
            and s.step == p["role"] and p["role"] not in s.acts:
        a = m.get("target")
        if a == 0 and p["role"] == "sheriff":
            pass  # xerife passou a vez
        elif isinstance(a, int) and a in s.players and s.players[a]["alive"] \
                and (a != p["id"] or p["role"] == "angel"):
            pass
        else:
            return None
        s.acts[p["role"]] = a
        s.todos()
        s.cv.notify_all()
    elif t == "vote" and s.phase == "vote" and p["alive"] and p["id"] not in s.votes:
        a = m.get("target")
        if a == 0 or (isinstance(a, int) and a in s.players
                      and s.players[a]["alive"] and a != p["id"]):
            s.votes[p["id"]] = a
            s.todos()
            s.cv.notify_all()
    return None


def sair(p, s):
    if not p:
        return
    p["conn"] = None
    if s.phase == "lobby":
        s.players.pop(p["id"], None)
    else:
        p["alive"] = False
    on = [q for q in s.players.values() if q["conn"]]
    if not on:
        s.rodando = False
        SALAS.pop(s.cod, None)
        s.cv.notify_all()
        return
    if s.host == p["id"]:
        s.host = on[0]["id"]
    s.todos()
    s.cv.notify_all()


def cliente(conn):
    p = s = None
    buf = b""
    try:
        while True:
            d = conn.recv(4096)
            if not d:
                break
            buf += d
            while b"\n" in buf:
                linha, buf = buf.split(b"\n", 1)
                try:
                    m = json.loads(linha.decode())
                except ValueError:
                    continue
                with LOCK:
                    r = tratar(conn, m, p, s)
                    if r:
                        p, s = r
    except OSError:
        pass
    finally:
        with LOCK:
            sair(p, s)
        conn.close()



def iniciar_servidor():
    """Liga o servidor em segundo plano. Se a porta já estiver em uso, outro servidor já está rodando."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((HOST, PORT))
    except OSError:
        return False
    srv.listen()

    def aceitar():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=cliente, args=(conn,), daemon=True).start()

    threading.Thread(target=aceitar, daemon=True).start()
    return True


def ip_local():
    try:
        t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        t.connect(("8.8.8.8", 80))
        ip = t.getsockname()[0]
        t.close()
        return ip
    except OSError:
        return "127.0.0.1"


pygame.init()
L, A = 1100, 720
tela = pygame.display.set_mode((L, A))
pygame.display.set_caption("MURDER HOGWARTS")
clock = pygame.time.Clock()

FUNDO = (20, 15, 35); ROXO = (48, 35, 68); DOURADO = (220, 180, 95)
BRANCO = (245, 240, 225); CINZA = (160, 150, 175); VERMELHO = (180, 55, 70)
VERDE = (75, 160, 100)
fonte = pygame.font.SysFont("georgia", 25)
titulo = pygame.font.SysFont("georgia", 44, bold=True)
pequena = pygame.font.SysFont("georgia", 17)
media = pygame.font.SysFont("georgia", 21)

PAPEIS = {"killer": "Assassino", "sheriff": "Xerife", "angel": "Anjo", "citizen": "Cidadão"}
COR_PAPEL = {"killer": (200, 40, 40), "sheriff": (220, 180, 60),
             "angel": (120, 180, 240), "citizen": (180, 200, 170)}
DESC = {
    "killer": "Elimine os outros sem ser descoberto. Toda noite você escolhe uma vítima.",
    "sheriff": "Descubra o assassino. Toda noite pode atirar em alguém ou passar. "
               "Se atirar num inocente, você morre de culpa.",
    "angel": "Toda noite você pode proteger um jogador, ou a si mesmo, do ataque.",
    "citizen": "Debata de dia e vote para expulsar o assassino.",
}

VERM, AMAR = (130, 25, 35), (220, 180, 60)
PERS = {
    "Harry": dict(pele=(240, 200, 165), cabelo=(25, 20, 20), est="curto", manto=VERM, faixa=AMAR, extras=["oculos", "cicatriz"]),
    "Hermione": dict(pele=(235, 195, 160), cabelo=(110, 70, 40), est="cacheado", manto=VERM, faixa=AMAR, extras=[]),
    "Rony": dict(pele=(245, 210, 180), cabelo=(205, 85, 30), est="curto", manto=VERM, faixa=AMAR, extras=["sardas"]),
    "Draco": dict(pele=(250, 222, 205), cabelo=(235, 225, 170), est="liso", manto=(30, 90, 60), faixa=(190, 195, 200), extras=[]),
    "Luna": dict(pele=(245, 210, 185), cabelo=(235, 215, 150), est="longo", manto=(40, 70, 140), faixa=(150, 110, 60), extras=["oculos"], lente=(240, 120, 200)),
    "Neville": dict(pele=(240, 200, 170), cabelo=(110, 80, 50), est="curto", manto=VERM, faixa=AMAR, extras=["bochechas"]),
    "Gina": dict(pele=(245, 210, 180), cabelo=(190, 60, 25), est="longo", manto=VERM, faixa=AMAR, extras=["sardas"]),
    "Fred": dict(pele=(245, 210, 180), cabelo=(215, 90, 30), est="curto", manto=VERM, faixa=AMAR, extras=["sardas"]),
    "George": dict(pele=(245, 210, 180), cabelo=(215, 90, 30), est="curto", manto=VERM, faixa=AMAR, extras=["sardas", "curativo"]),
    "Cho": dict(pele=(240, 205, 175), cabelo=(15, 15, 25), est="longo", manto=(40, 70, 140), faixa=(150, 110, 60), extras=[]),
    "Cedrico": dict(pele=(240, 205, 175), cabelo=(90, 60, 40), est="curto", manto=(55, 50, 45), faixa=(240, 200, 50), extras=[]),
    "Professor": dict(pele=(235, 200, 175), cabelo=(230, 230, 235), est="longo", manto=(70, 45, 110), faixa=DOURADO, extras=["barba", "chapeu"]),
}
NOMES_P = list(PERS)


def cinza(c):
    g = int(sum(c) / 3 * 0.8)
    return (g, g, g + 8)


def desenhar(surf, nome, cx, cy, r=30, vivo=True):
    p = PERS.get(nome, PERS["Harry"])
    f = (lambda c: c) if vivo else cinza
    R = lambda x, y, w, h: pygame.Rect(int(x), int(y), int(w), int(h))
    pele, cab, manto, faixa = f(p["pele"]), f(p["cabelo"]), f(p["manto"]), f(p["faixa"])
    ex, est = p["extras"], p["est"]
    esc = (30, 25, 30)
    # corpo e gola
    pygame.draw.ellipse(surf, manto, R(cx - 1.3 * r, cy + 0.8 * r, 2.6 * r, 2.2 * r))
    pygame.draw.polygon(surf, f((235, 235, 235)), [(cx - .3 * r, cy + .85 * r), (cx + .3 * r, cy + .85 * r), (cx, cy + 1.6 * r)])
    w = max(2, int(r * .12))
    pygame.draw.line(surf, faixa, (cx - .3 * r, cy + .85 * r), (cx, cy + 1.6 * r), w)
    pygame.draw.line(surf, faixa, (cx + .3 * r, cy + .85 * r), (cx, cy + 1.6 * r), w)
    # cabelo de trás
    if est == "longo":
        pygame.draw.ellipse(surf, cab, R(cx - 1.1 * r, cy - 1.15 * r, 2.2 * r, 2.9 * r))
    if est == "cacheado":
        pygame.draw.ellipse(surf, cab, R(cx - 1.2 * r, cy - 1.2 * r, 2.4 * r, 2.6 * r))
    pygame.draw.circle(surf, pele, (cx, cy), r)
    if "curativo" in ex:
        pygame.draw.rect(surf, f((245, 245, 245)), R(cx - 1.08 * r, cy - .1 * r, .3 * r, .5 * r))
    if "barba" in ex:
        pygame.draw.ellipse(surf, f((235, 235, 235)), R(cx - .75 * r, cy + .3 * r, 1.5 * r, 1.3 * r))
    # cabelo da frente
    pygame.draw.ellipse(surf, cab, R(cx - 1.04 * r, cy - 1.1 * r, 2.08 * r, 1.05 * r))
    if est == "cacheado":
        for dx in (-.8, -.4, 0, .4, .8):
            pygame.draw.circle(surf, cab, (cx + dx * r, cy - (.95 - abs(dx) * .3) * r), .35 * r)
    # olhos
    for sx in (-1, 1):
        ex_, ey = cx + sx * .38 * r, cy + .1 * r
        if vivo:
            pygame.draw.circle(surf, esc, (ex_, ey), max(2, .1 * r))
        else:
            d = .1 * r
            pygame.draw.line(surf, esc, (ex_ - d, ey - d), (ex_ + d, ey + d), 2)
            pygame.draw.line(surf, esc, (ex_ - d, ey + d), (ex_ + d, ey - d), 2)
    if vivo:
        pygame.draw.arc(surf, esc, R(cx - .3 * r, cy + .3 * r, .6 * r, .35 * r), math.pi, 2 * math.pi, 2)
    else:
        pygame.draw.line(surf, esc, (cx - .25 * r, cy + .55 * r), (cx + .25 * r, cy + .55 * r), 2)
    # extras
    if "oculos" in ex:
        lente = f(p.get("lente", (30, 30, 30)))
        for sx in (-1, 1):
            pygame.draw.circle(surf, lente, (cx + sx * .38 * r, cy + .1 * r), .27 * r, 2)
        pygame.draw.line(surf, lente, (cx - .11 * r, cy + .1 * r), (cx + .11 * r, cy + .1 * r), 2)
    if "cicatriz" in ex:
        pts = [(cx + .1 * r, cy - .5 * r), (cx - .05 * r, cy - .3 * r), (cx + .08 * r, cy - .3 * r), (cx - .06 * r, cy - .1 * r)]
        pygame.draw.lines(surf, f((200, 40, 40)), False, pts, 2)
    if "sardas" in ex:
        for dx, dy in ((-.55, .35), (-.4, .45), (-.25, .35), (.25, .35), (.4, .45), (.55, .35)):
            pygame.draw.circle(surf, f((170, 100, 80)), (cx + dx * r, cy + dy * r), max(1, .05 * r))
    if "bochechas" in ex:
        for sx in (-1, 1):
            pygame.draw.circle(surf, f((235, 140, 140)), (cx + sx * .55 * r, cy + .4 * r), .17 * r)
    if "chapeu" in ex:
        h = f((40, 25, 70))
        pygame.draw.polygon(surf, h, [(cx - 1.2 * r, cy - .7 * r), (cx + 1.2 * r, cy - .7 * r), (cx + .1 * r, cy - 2.5 * r)])
        pygame.draw.ellipse(surf, h, R(cx - 1.4 * r, cy - .95 * r, 2.8 * r, .5 * r))
        pygame.draw.rect(surf, f(DOURADO), R(cx - 1.0 * r, cy - 0.85 * r, 2.0 * r, .12 * r))


# ---------------------------------------------------------------- estado
st = dict(estado="menu", nick="", codigo="", endereco="127.0.0.1", campo="nick", pers=0,
          erro="", conn=None, entrou=False, jogadores=[], meu_id=None, cargo="",
          fase="lobby", passo="", msg="", host=None, sala="", tempo=0, t_recv=0,
          votaram=set(), vivo=True, agi=False, alvo=None, passo_t=0)
mensagens, lock = [], threading.Lock()
botoes, areas = [], []


def resetar():
    st.update(jogadores=[], fase="lobby", passo="", cargo="", alvo=None,
              votaram=set(), entrou=False, vivo=True, agi=False)


def restante():
    return max(0, st["tempo"] - int(time.time() - st["t_recv"]))


# ---------------------------------------------------------------- rede
def receber(c):
    buf = b""
    try:
        while True:
            d = c.recv(4096)
            if not d:
                break
            buf += d
            while b"\n" in buf:
                linha, buf = buf.split(b"\n", 1)
                try:
                    with lock:
                        mensagens.append(json.loads(linha.decode()))
                except ValueError:
                    pass
    except OSError:
        pass
    if st["conn"] is c:
        with lock:
            mensagens.append({"type": "disconnected"})


def enviar(d):
    c = st["conn"]
    if c:
        try:
            c.sendall((json.dumps(d, ensure_ascii=False) + "\n").encode())
        except OSError:
            pass


def fechar():
    c, st["conn"] = st["conn"], None
    if c:
        try:
            c.close()
        except OSError:
            pass


def conectar():
    host, porta = st["endereco"].strip() or "127.0.0.1", 8765
    if ":" in host:
        host, p = host.rsplit(":", 1)
        porta = int(p) if p.isdigit() else 8765
    st["estado"], st["erro"] = "conectando", ""

    def trab():
        try:
            c = socket.create_connection((host, porta), timeout=5)
            c.settimeout(None)
        except OSError:
            with lock:
                mensagens.append({"type": "disconnected", "message": "Não foi possível conectar. Verifique o servidor."})
            return
        st["conn"] = c
        threading.Thread(target=receber, args=(c,), daemon=True).start()
        enviar({"type": "join", "nick": st["nick"].strip(),
                "character": NOMES_P[st["pers"]], "room": st["codigo"]})

    threading.Thread(target=trab, daemon=True).start()


def atualizar_rede():
    with lock:
        novas = mensagens[:]
        mensagens.clear()
    for m in novas:
        t = m.get("type")
        if t == "state":
            novo = (m["phase"], m["step"])
            if novo != (st["fase"], st["passo"]):
                st["passo_t"], st["alvo"] = time.time(), None
            st.update(entrou=True, jogadores=m["players"], meu_id=m["my_id"], cargo=m["my_role"],
                      fase=m["phase"], passo=m["step"], msg=m["message"], host=m["host"],
                      sala=m["room"], tempo=m["time_left"], t_recv=time.time(),
                      votaram=set(m["voted"]), vivo=m["alive"], agi=m["acted"])
            st["estado"] = "sala" if m["phase"] == "lobby" else "jogo"
            st["erro"] = ""
        elif t == "error":
            st["erro"] = m.get("message", "Erro")
            if not st["entrou"]:
                fechar()
                st["estado"] = "menu"
        elif t == "disconnected":
            fechar()
            resetar()
            st["estado"] = "menu"
            st["erro"] = m.get("message", "Conexão encerrada.")


# ---------------------------------------------------------------- desenho
def escrever(texto, x, y, cor=BRANCO, f=fonte, centro=True):
    img = f.render(str(texto), True, cor)
    ret = img.get_rect()
    if centro:
        ret.center = (x, y)
    else:
        ret.topleft = (x, y)
    tela.blit(img, ret)


def multi(texto, cx, y, larg, cor=BRANCO, f=pequena):
    linhas, atual = [], ""
    for pal in str(texto).split():
        t = (atual + " " + pal).strip()
        if f.size(t)[0] > larg and atual:
            linhas.append(atual)
            atual = pal
        else:
            atual = t
    linhas.append(atual)
    for i, l in enumerate(linhas):
        escrever(l, cx, y + i * (f.get_linesize()), cor, f)


def botao(texto, x, y, w, h, acao, ativo=True, cor=DOURADO):
    ret = pygame.Rect(x, y, w, h)
    pygame.draw.rect(tela, ROXO if ativo else (40, 35, 50), ret, border_radius=12)
    pygame.draw.rect(tela, cor if ativo else CINZA, ret, 2, border_radius=12)
    escrever(texto, ret.centerx, ret.centery, BRANCO if ativo else CINZA, pequena)
    if ativo:
        botoes.append((ret, acao))


def fundo():
    tela.fill(FUNDO)
    pygame.draw.rect(tela, ROXO, (25, 20, 1050, 680), border_radius=25)
    pygame.draw.rect(tela, DOURADO, (25, 20, 1050, 680), 2, border_radius=25)
    escrever("MURDER HOGWARTS", 550, 75, DOURADO, titulo)


def nome_alvo():
    for j in st["jogadores"]:
        if j["id"] == st["alvo"]:
            return j["nick"]
    return ""


def mesa(validos=(), escuro=False):
    areas.clear()
    cor = (45, 28, 30) if escuro else (95, 60, 55)
    pygame.draw.ellipse(tela, cor, (260, 310, 580, 180))
    pygame.draw.ellipse(tela, DOURADO, (260, 310, 580, 180), 3)
    n = max(1, len(st["jogadores"]))
    for i, j in enumerate(st["jogadores"]):
        a = 2 * math.pi * i / n - math.pi / 2
        x, y = 550 + int(340 * math.cos(a)), 400 + int(165 * math.sin(a))
        vivo = j["alive"]
        if j["id"] == st["alvo"]:
            pygame.draw.circle(tela, VERMELHO, (x, y), 40, 3)
        elif j["id"] in validos:
            pygame.draw.circle(tela, (130, 115, 70), (x, y), 38, 1)
        desenhar(tela, j["character"], x, y, 24, vivo)
        if j["id"] in st["votaram"]:
            pygame.draw.circle(tela, VERDE, (x + 30, y - 24), 10)
            pygame.draw.lines(tela, BRANCO, False, [(x + 25, y - 24), (x + 29, y - 19), (x + 36, y - 29)], 2)
        eu = j["id"] == st["meu_id"]
        escrever(j["nick"][:12] + (" (você)" if eu else ""), x, y + 76,
                 BRANCO if vivo else CINZA, pequena)
        if j.get("role"):
            escrever(PAPEIS[j["role"]], x, y + 94, COR_PAPEL[j["role"]], pequena)
        if j["id"] in validos:
            areas.append((x, y, j["id"]))


def olho(f):
    f = min(1, max(0.03, f))
    cx, cy = 550, 360
    cor = COR_PAPEL[st["cargo"]]
    h = max(2, int(150 * f))
    pygame.draw.ellipse(tela, (235, 230, 220), (cx - 170, cy - h // 2, 340, h))
    if h > 8:
        tela.set_clip(pygame.Rect(cx - 170, cy - h // 2, 340, h))
        pygame.draw.circle(tela, cor, (cx, cy), 55)
        pygame.draw.circle(tela, (10, 5, 5), (cx, cy), 22)
        tela.set_clip(None)
    pygame.draw.ellipse(tela, (90, 90, 110), (cx - 170, cy - h // 2, 340, h), 3)


def tela_menu():
    fundo()
    escrever("Entre na escola de magia", 550, 125, DOURADO, media)
    nome = NOMES_P[st["pers"]]
    desenhar(tela, nome, 230, 330, 55)
    escrever(nome, 230, 535, DOURADO, fonte)
    escrever("<", 125, 535, DOURADO, titulo)
    escrever(">", 335, 535, DOURADO, titulo)
    botoes.append((pygame.Rect(95, 510, 60, 50), "esq"))
    botoes.append((pygame.Rect(305, 510, 60, 50), "dir"))
    campos = [("nick", "Nick", st["nick"]),
              ("room", "Código da sala (vazio = criar nova)", st["codigo"]),
              ("endereco", "Servidor (IP ou IP:porta)", st["endereco"])]
    for i, (c, rot, val) in enumerate(campos):
        y = 185 + i * 85
        ativo = st["campo"] == c
        escrever(rot, 440, y - 24, CINZA, pequena, False)
        ret = pygame.Rect(440, y, 560, 44)
        pygame.draw.rect(tela, (25, 20, 40), ret, border_radius=8)
        pygame.draw.rect(tela, DOURADO if ativo else CINZA, ret, 2, border_radius=8)
        escrever(val + ("|" if ativo and int(time.time() * 2) % 2 else ""), 452, y + 10, BRANCO, pequena, False)
        botoes.append((ret, "campo:" + c))
    botao("ENTRAR NA SALA", 570, 440, 300, 52, "entrar")
    multi(st["erro"], 720, 525, 560, VERMELHO)
    escrever("Tab alterna os campos  •  setas ← → trocam o personagem", 550, 665, CINZA, pequena)


def tela_sala():
    fundo()
    escrever(f"Sala: {st['sala']}  (compartilhe este código)", 550, 130, DOURADO, media)
    escrever(f"Jogadores: {len(st['jogadores'])}/12", 550, 163, CINZA, pequena)
    if SERVIDOR_LOCAL:
        escrever(f"Você é o servidor! Amigos digitam no campo Servidor: {ip_local()}", 550, 190, VERDE, pequena)
    for i, j in enumerate(st["jogadores"]):
        x, y = 250 + (i % 4) * 200, 240 + (i // 4) * 130
        desenhar(tela, j["character"], x, y, 28)
        escrever(j["nick"][:14], x, y + 88, DOURADO if j["id"] == st["host"] else BRANCO, pequena)
    if st["meu_id"] == st["host"]:
        n = len(st["jogadores"])
        botao("COMEÇAR PARTIDA" if n >= 4 else "Mínimo de 4 jogadores", 410, 620, 280, 50, "comecar", n >= 4)
    else:
        escrever("Aguardando o anfitrião (nick dourado) iniciar...", 550, 645, CINZA, pequena)
    escrever(st["erro"], 550, 688, VERMELHO, pequena)


def tela_papel():
    fundo()
    c = st["cargo"]
    meu = next((j for j in st["jogadores"] if j["id"] == st["meu_id"]), None)
    escrever("Seu papel é...", 550, 150, CINZA, media)
    if meu:
        desenhar(tela, meu["character"], 550, 280, 70)
    escrever(PAPEIS.get(c, "?").upper(), 550, 515, COR_PAPEL.get(c, BRANCO), titulo)
    multi(DESC.get(c, ""), 550, 565, 640, BRANCO, media)
    escrever("Não conte a ninguém!", 550, 665, VERMELHO, pequena)


def tela_noite():
    tela.fill((4, 4, 10))
    passo, cargo = st["passo"], st["cargo"]
    t = time.time() - st["passo_t"]
    escrever(st["msg"], 550, 80, (150, 150, 200), fonte)
    agindo = passo == cargo and st["vivo"] and not st["agi"]
    if not agindo:
        pygame.draw.circle(tela, (225, 225, 195), (550, 330), 45)
        pygame.draw.circle(tela, (4, 4, 10), (572, 318), 40)
        escrever("Mantenha os olhos fechados..." if passo != "sleep" else "Zzz...", 550, 440, (90, 90, 130), media)
        return
    escrever(f"{restante()}s", 1030, 40, DOURADO, media)
    if t < 3.5:
        olho(t / 3.0)
        escrever("Você abre os olhos devagar...", 550, 520, (120, 120, 160), media)
        return
    vivos = {j["id"] for j in st["jogadores"] if j["alive"]}
    validos = vivos if cargo == "angel" else vivos - {st["meu_id"]}
    mesa(validos, True)
    tit = {"killer": "Escolha sua vítima", "sheriff": "Atirar em alguém ou passar?",
           "angel": "Quem você vai proteger? (pode ser você)"}[cargo]
    escrever(tit, 550, 345, COR_PAPEL[cargo], media)
    escrever(nome_alvo() or "- selecione um jogador -", 550, 385, BRANCO, pequena)
    a = st["alvo"] is not None
    if cargo == "killer":
        botao("MATAR", 450, 420, 200, 48, "matar", a, VERMELHO)
    elif cargo == "sheriff":
        botao("ATIRAR", 380, 420, 160, 48, "atirar", a, VERMELHO)
        botao("PASSAR", 560, 420, 160, 48, "passar")
    else:
        botao("PROTEGER", 450, 420, 200, 48, "proteger", a, COR_PAPEL["angel"])


def tela_mesa():
    fundo()
    f = st["fase"]
    nomes = {"day": "DEBATE", "vote": "VOTAÇÃO", "vote_result": "RESULTADO", "end": "FIM DE JOGO"}
    escrever(nomes.get(f, ""), 550, 125, DOURADO, fonte)
    cargo = PAPEIS.get(st["cargo"], "?") + ("" if st["vivo"] else " (eliminado)")
    escrever(f"Seu cargo: {cargo}", 550, 155, CINZA, pequena)
    if f != "end":
        escrever(f"{restante()}s", 1030, 60, DOURADO, media)
    pode = f == "vote" and st["vivo"] and not st["agi"]
    validos = {j["id"] for j in st["jogadores"] if j["alive"] and j["id"] != st["meu_id"]} if pode else set()
    mesa(validos)
    multi(st["msg"], 550, 335, 520, BRANCO)
    if f == "vote":
        if pode:
            escrever(nome_alvo() or "- clique em quem você acha que é o assassino -", 550, 395, DOURADO, pequena)
            botao("VOTAR", 390, 430, 150, 44, "votar", st["alvo"] is not None, VERMELHO)
            botao("PULAR", 560, 430, 150, 44, "pular")
        else:
            escrever("Voto registrado." if st["vivo"] else "Você foi eliminado: apenas observe.", 550, 430, CINZA, pequena)
    elif f == "end":
        escrever("Voltando à sala em instantes...", 550, 440, CINZA, pequena)


def desenhar_tela():
    botoes.clear()
    e = st["estado"]
    if e == "menu":
        tela_menu()
    elif e == "conectando":
        fundo()
        escrever("Conectando ao servidor...", 550, 350, DOURADO)
    elif e == "sala":
        tela_sala()
    elif e == "jogo":
        f = st["fase"]
        tela_papel() if f == "roles" else tela_noite() if f == "night" else tela_mesa()


# ---------------------------------------------------------------- entrada
def executar(a):
    if a == "entrar":
        if st["nick"].strip():
            conectar()
        else:
            st["erro"] = "Digite seu nick primeiro!"
    elif a == "esq":
        st["pers"] = (st["pers"] - 1) % len(NOMES_P)
    elif a == "dir":
        st["pers"] = (st["pers"] + 1) % len(NOMES_P)
    elif a.startswith("campo:"):
        st["campo"] = a[6:]
    elif a == "comecar":
        enviar({"type": "start"})
    elif a in ("matar", "atirar", "proteger") and st["alvo"]:
        enviar({"type": "night_action", "target": st["alvo"]})
    elif a == "passar":
        enviar({"type": "night_action", "target": 0})
    elif a == "votar" and st["alvo"]:
        enviar({"type": "vote", "target": st["alvo"]})
    elif a == "pular":
        enviar({"type": "vote", "target": 0})


def tecla(ev):
    chaves = {"nick": "nick", "room": "codigo", "endereco": "endereco"}
    ordem = ["nick", "room", "endereco"]
    c = st["campo"]
    k = chaves[c]
    if ev.key == pygame.K_BACKSPACE:
        st[k] = st[k][:-1]
    elif ev.key == pygame.K_TAB:
        st["campo"] = ordem[(ordem.index(c) + 1) % 3]
    elif ev.key == pygame.K_RETURN:
        executar("entrar")
    elif ev.key == pygame.K_LEFT:
        executar("esq")
    elif ev.key == pygame.K_RIGHT:
        executar("dir")
    elif ev.unicode and ev.unicode.isprintable():
        v = st[k] + ev.unicode
        if c == "room":
            v = v.upper()
        st[k] = v[:{"nick": 18, "room": 8, "endereco": 80}[c]]


def main():
    rodando = True
    while rodando:
        atualizar_rede()
        desenhar_tela()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                rodando = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                x, y = ev.pos
                for ret, acao in botoes:
                    if ret.collidepoint(x, y):
                        executar(acao)
                        break
                else:
                    for px, py, i in areas:
                        if math.hypot(x - px, y - py) < 42:
                            st["alvo"] = i
            elif ev.type == pygame.KEYDOWN and st["estado"] == "menu":
                tecla(ev)
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE and st["estado"] in ("sala", "jogo", "conectando"):
                fechar()
                resetar()
                st["estado"], st["erro"] = "menu", ""
        pygame.display.flip()
        clock.tick(60)
    fechar()
    pygame.quit()
    sys.exit()


SERVIDOR_LOCAL = False

if __name__ == "__main__":
    SERVIDOR_LOCAL = iniciar_servidor()
    main()