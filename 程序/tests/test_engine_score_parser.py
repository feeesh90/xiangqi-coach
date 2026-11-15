import re
line="info depth 18 multipv 1 score cp 123 nodes 100 pv h2e2 h9g7"
sm=re.search(r"(?:^|\s)score\s+cp\s+(-?\d+)",line)
assert sm and int(sm.group(1))==123
line2="info depth 20 multipv 1 score mate -3 pv e0e1"
mm=re.search(r"(?:^|\s)score\s+mate\s+(-?\d+)",line2)
assert mm and int(mm.group(1))==-3
print("ENGINE_SCORE_REGEX_OK")
