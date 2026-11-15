from rules import Position, Move, legal_moves, in_check, pseudo_moves, piece_name, move_desc, chinese_notation

VALUES={"R":500,"N":270,"B":120,"A":120,"C":285,"P":70,"K":10000}

def side_name(red): return "红方" if red else "黑方"

def _attacked_enemy_pieces(after,moved_r,moved_c):
    p=after.b[moved_r][moved_c]
    if not p:return []
    red=p.isupper()
    seen=[]
    for a in pseudo_moves(after,moved_r,moved_c):
        q=after.b[a.tr][a.tc]
        if q and q.isupper()!=red:
            seen.append(q)
    return seen

def feature_text(pos,m):
    p=pos.b[m.fr][m.fc]
    target=pos.b[m.tr][m.tc]
    t=p.upper()
    after=pos.copy()
    after.apply_unchecked(m)
    bits=[]

    if target:
        bits.append(f"直接吃掉对方{piece_name(target)}")
    if in_check(after, after.red):
        bits.append("落子后形成将军，对方必须先处理将帅安全")

    attacked=_attacked_enemy_pieces(after,m.tr,m.tc)
    important=[]
    for q in attacked:
        if q.upper() in ("R","C","N"):
            important.append(piece_name(q))
    if important:
        uniq=[]
        for x in important:
            if x not in uniq:uniq.append(x)
        bits.append("同时攻击对方的"+"、".join(uniq[:2]))

    if t=="N":
        if (p.isupper() and m.fr>=8) or (p.islower() and m.fr<=1):
            bits.append("把马从后场发展出来，增加中心和侧翼控制")
        else:
            bits.append("把马调整到更有价值的控制点")
    elif t=="R":
        bits.append("让车进入更通畅的横线或纵线，扩大控制范围")
    elif t=="C":
        if m.fc!=m.tc:
            bits.append("改变炮线和炮架，重组中路或侧翼压力")
        else:
            bits.append("沿纵线调炮，寻找新的炮架与牵制机会")
    elif t=="P":
        crossed=m.tr<=4 if p.isupper() else m.tr>=5
        bits.append("推进兵卒抢空间"+("，而且已经过河，后续可以横走" if crossed else ""))
    elif t in ("A","B"):
        bits.append("改善防守阵型和将帅安全")
    elif t=="K":
        bits.append("这是将帅安全或具体战术相关的处理")

    if 2<=m.tc<=6 and 2<=m.tr<=7 and t in ("N","R","C","P"):
        bits.append("落点靠近关键区域，方便后续子力协同")

    if not bits:
        bits.append("这步主要改善子力位置，并保持后续选择")
    return "；".join(bits)+"。"

def reply_consequence(pos,m):
    """
    Describe the concrete immediate consequence of a move.
    Used heavily when explaining opponent's strongest reply.
    """
    p=pos.b[m.fr][m.fc]
    target=pos.b[m.tr][m.tc]
    after=pos.copy();after.apply_unchecked(m)
    bits=[]
    if target:
        bits.append(f"直接吃掉{piece_name(target)}")
    if in_check(after,after.red):
        bits.append("同时形成将军")
    attacked=_attacked_enemy_pieces(after,m.tr,m.tc)
    important=[piece_name(q) for q in attacked if q.upper() in ("R","C","N")]
    if important:
        uniq=[]
        for x in important:
            if x not in uniq:uniq.append(x)
        bits.append("并继续攻击"+"、".join(uniq[:2]))
    if not bits:
        bits.append(feature_text(pos,m).rstrip("。"))
    return "；".join(bits)+"。"

def pv_steps(pos,pv,max_plies=8):
    p=pos.copy()
    out=[]
    for i,u in enumerate(pv[:max_plies],1):
        m=Move.from_ucci(u)
        if not m or m not in legal_moves(p): break
        out.append({
            "n":i,
            "side":side_name(p.red),
            "move":m,
            "desc":move_desc(p,m),
            "why":feature_text(p,m)
        })
        p.apply_unchecked(m)
    return out

def best_explanation(pos,analysis):
    c=analysis.candidates[0] if analysis.candidates else None
    best=analysis.best
    pv=c.pv if c else [best.ucci()]
    steps=pv_steps(pos,pv,10)
    lines=[
        "【推荐怎么走】",
        move_desc(pos,best),
        "",
        "【这一步具体好在哪里】",
        feature_text(pos,best),
    ]

    if len(steps)>=2:
        # reconstruct reply position from root
        p1=pos.copy();p1.apply_unchecked(best)
        reply=steps[1]["move"]
        lines += [
            "",
            "【对方最强回应】",
            move_desc(p1,reply),
            reply_consequence(p1,reply)
        ]

    if len(steps)>=3:
        p2=pos.copy()
        p2.apply_unchecked(steps[0]["move"])
        p2.apply_unchecked(steps[1]["move"])
        nxt=steps[2]["move"]
        lines += [
            "",
            "【我方下一步计划】",
            move_desc(p2,nxt),
            feature_text(p2,nxt)
        ]

    if len(analysis.candidates)>1:
        lines += ["","【另外两种强方案】"]
        for cand in analysis.candidates[1:3]:
            lines.append("· "+move_desc(pos,cand.move))
    return "\n".join(lines)

def score_strength(candidate):
    if candidate is None: return None
    if candidate.mate is not None:
        # huge internal magnitude; sign follows engine root POV
        return 100000 if candidate.mate>0 else -100000
    return candidate.score_cp

def classify_user_move(best_analysis, forced_analysis):
    best=best_analysis.candidates[0] if best_analysis.candidates else None
    mine=forced_analysis.candidates[0] if forced_analysis.candidates else None
    if not best or not mine:
        return "可下，但暂时无法稳定比较", None
    if best.move == mine.move:
        return "符合 当前引擎 首选", 0
    a=score_strength(best); b=score_strength(mine)
    if a is None or b is None:
        return "不是首选，但属于引擎可计算方案", None
    loss=max(0,a-b)
    if loss < 45: label="非常接近首选"
    elif loss < 120: label="可下，但不是首选"
    elif loss < 260: label="有明显损失"
    else: label="风险很大，建议重点复盘"
    return label,loss

def forced_reply_from_pv(before,candidate,forced_analysis):
    """Return the opponent reply and continuation directly from the forced
    search PV. This removes an entire extra engine search from pre-move/review."""
    if not forced_analysis or not forced_analysis.candidates:
        return None,[]
    c=forced_analysis.candidates[0]
    pv=c.pv or []
    if not pv or pv[0]!=candidate.ucci() or len(pv)<2:
        return None,[]
    after=before.copy();after.apply_unchecked(candidate)
    reply=Move.from_ucci(pv[1])
    if not reply or reply not in legal_moves(after):
        return None,[]
    return reply,pv[1:]

def user_move_report(before,user_move,best_analysis,forced_analysis,reply_analysis=None):
    label,_=classify_user_move(best_analysis,forced_analysis)
    lines=["【你的这一步】",move_desc(before,user_move),"","【AI判断】",label]
    if best_analysis.best != user_move:
        lines += ["","【当前引擎 更推荐】",move_desc(before,best_analysis.best),feature_text(before,best_analysis.best)]

    after=before.copy();after.apply_unchecked(user_move)
    reply=None;continuation=[]
    if reply_analysis and reply_analysis.best:
        reply=reply_analysis.best
        c=reply_analysis.candidates[0] if reply_analysis.candidates else None
        continuation=c.pv if c else []
    else:
        reply,continuation=forced_reply_from_pv(before,user_move,forced_analysis)
    if reply:
        lines += ["","【你这样走后，对方最强回应】",move_desc(after,reply),reply_consequence(after,reply)]
        steps=pv_steps(after,continuation,6) if continuation else []
        if steps:
            lines += ["","【继续往后看】"]
            for st in steps[:4]:lines.append(f"{st['n']}. {st['side']}：{st['desc']}")
    return "\n".join(lines)

def premove_report(before,candidate,best_analysis,forced_analysis,reply_analysis=None):
    label,_=classify_user_move(best_analysis,forced_analysis)
    lines=["【你准备试走】",move_desc(before,candidate),"","【先告诉你结论】",label]
    if best_analysis.best==candidate:
        lines+=["","这就是 当前引擎 当前首选，可以作为主要方案继续看。"]
    else:
        lines+=["","【当前引擎 当前首选】",move_desc(before,best_analysis.best),feature_text(before,best_analysis.best)]
    after=before.copy();after.apply_unchecked(candidate)
    reply=None;continuation=[]
    if reply_analysis and reply_analysis.best:
        reply=reply_analysis.best
        c=reply_analysis.candidates[0] if reply_analysis.candidates else None
        continuation=c.pv if c else []
    else:
        reply,continuation=forced_reply_from_pv(before,candidate,forced_analysis)
    if reply:
        lines+=["","【如果你真这么走，对方最强应手】",move_desc(after,reply),reply_consequence(after,reply)]
        steps=pv_steps(after,continuation,6) if continuation else []
        if steps:
            lines+=["","【后续主要变化】"]
            for st in steps[:5]:lines.append(f"{st['n']}. {st['side']}：{st['desc']}")
    lines+=["","这只是预走分析，正式棋盘没有改变。"]
    return "\n".join(lines)

def threat_report(pos,analysis):
    """
    Explain the current position from the side-to-move perspective without
    showing numeric evaluation.
    """
    best=analysis.best
    c=analysis.candidates[0] if analysis.candidates else None
    pv=c.pv if c else [best.ucci()]
    steps=pv_steps(pos,pv,8)
    lines=[
        "【当前最重要的处理】",
        move_desc(pos,best),
        feature_text(pos,best),
    ]
    if len(steps)>=2:
        p1=pos.copy();p1.apply_unchecked(best)
        reply=steps[1]["move"]
        lines += [
            "",
            "【如果按最佳方案走，对方最强回应】",
            move_desc(p1,reply),
            reply_consequence(p1,reply)
        ]
    if len(analysis.candidates)>1:
        lines += ["","【可以考虑的其他强着】"]
        for x in analysis.candidates[1:3]:
            lines.append("· "+move_desc(pos,x.move))
    lines += [
        "",
        "提示：这里不显示数字分数，最终判断来自 当前引擎 的搜索顺序和主变化线。"
    ]
    return "\n".join(lines)


def candidate_report(pos,candidate,rank=1,is_best=False):
    """Human-readable explanation for one concrete MultiPV plan."""
    pv=candidate.pv or [candidate.move.ucci()]
    steps=pv_steps(pos,pv,10)
    title="首选方案" if is_best else f"对比方案 {rank}"
    lines=[
        f"【{title}】",
        move_desc(pos,candidate.move),
        "",
        "【这一方案的核心】",
        feature_text(pos,candidate.move),
    ]
    if not is_best:
        lines += ["", "说明：这是强候选，用来比较思路；最终首选仍是方案1。"]
    if len(steps)>=2:
        p1=pos.copy();p1.apply_unchecked(steps[0]["move"])
        r=steps[1]["move"]
        lines += ["","【对方最强回应】",move_desc(p1,r),reply_consequence(p1,r)]
    if len(steps)>=3:
        p2=pos.copy();p2.apply_unchecked(steps[0]["move"]);p2.apply_unchecked(steps[1]["move"])
        m=steps[2]["move"]
        lines += ["","【再往下一步】",move_desc(p2,m),feature_text(p2,m)]
    lines += ["","【这条方案的主变化】"]
    for st in steps[:10]:
        lines.append(f"{st['n']}. {st['side']}：{st['desc']}")
    return "\n".join(lines)
