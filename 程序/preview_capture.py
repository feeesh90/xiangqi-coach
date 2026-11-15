from app import App
from engine import Analysis, Candidate
from rules import Position, Move
from PIL import ImageGrab

app=App()
app.geometry('1280x860+20+20')
app.update()
app.after(250, lambda: app._set_engine_badge('Pikafish 已就绪', True))
app.after(260, lambda: app.book_badge.configure(text='大师棋谱：53953盘 · SQLite大师库'))

snap=Position()
c1=Candidate(1, Move.from_ucci('h2e2'), ['h2e2','h9g7','h0g2','i9h9','i0h0','b9c7'], 28, 32, None)
c2=Candidate(2, Move.from_ucci('c3c4'), ['c3c4','h9g7','h0g2','i9h9','i0h0','b9c7'], 26, 18, None)
c3=Candidate(3, Move.from_ucci('h0g2'), ['h0g2','h9g7','c3c4','i9h9','i0h0','b9c7'], 25, 12, None)
a=Analysis(c1.move,[c1,c2,c3],28,15.2)

def show():
    app._show_recommendation(a,snap)
    app.status.configure(text='分析完成 · 深度 28 · 15.2 秒')
    app.update_idletasks(); app.update()

def capture():
    app.update_idletasks(); app.update()
    # Window is explicitly placed at +20+20, size 1280x860.
    im=ImageGrab.grab(bbox=(20,20,1300,880))
    im.save('/mnt/data/象棋指导棋_V5_效果预览.png')
    app.after(100,app.destroy)

app.after(400,show)
app.after(1100,capture)
app.mainloop()
