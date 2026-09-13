"""Generate deterministic fixtures from invented values, without reading a save.

The binary has the parser's documented structural layout but starts entirely
zeroed. OCR dictionaries are constructed from labels and an artificial grid;
no screenshot, private OCR output, conversation, or user record is an input.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import save_reader_v2 as reader

FIXTURES = ROOT / "tests/fixtures"


def build() -> tuple[bytes, dict]:
    date = {"year": 2030, "month": 5, "day": 12, "career_year": 2}
    header = bytearray(reader._HEADER_SIZE)
    for offset, text in ((72, "TEST TEAM 2030년 5월 12일(2년 차)"), (584, "Test Player(투수)")):
        encoded = text.encode("utf-8")
        header[offset:offset + len(encoded)] = encoded
    rows_end = reader._CAREER_SUMMARY_BASE + reader._CAREER_SUMMARY_STRIDE * 3
    profiles_at = rows_end + 0x100
    plain = bytearray(profiles_at + 0x130 * 2 + 0x40)
    previous = {
        0x00:75, 0x3C:55, 0x40:10, 0x46:12, 0x48:250, 0x4A:80,
        0x50:12, 0x54:5, 0x56:3, 0x64:16, 0x70:40, 0x78:20,
        0x7A:9, 0x7C:4, 0x7E:15, 0x80:32, 0x82:1, 0x84:38,
        0x86:12, 0x88:145, 0x8C:2, 0x8E:3, 0x96:1,
    }
    current = {**previous, 0x00:54, 0x3C:40, 0x48:190, 0x4A:60, 0x50:9,
               0x54:4, 0x56:2, 0x88:120, 0x84:27, 0x7A:6, 0x7C:3}
    for index, values in enumerate((previous, current)):
        base = reader._CAREER_SUMMARY_BASE + index * reader._CAREER_SUMMARY_STRIDE
        for offset, value in values.items():
            struct.pack_into("<H", plain, base + offset, value)
    for index in range(2):
        base = profiles_at + index * 0x130
        plain[base] = 0x25
        struct.pack_into("<I", plain, base + 8, 200001)
        names = b"Player\0Test\0T.Player\0"
        plain[base + 0x24:base + 0x24 + len(names)] = names
        struct.pack_into("<H", plain, base + 0x128, 2008)
    stats = reader._season_summary_stats(plain, reader._CAREER_SUMMARY_BASE + reader._CAREER_SUMMARY_STRIDE)
    expected = {
        "source": "synthetic-from-zero; no user save or screenshot input",
        "team": "TEST TEAM", "player_name": "Test Player", "display_name": "Test Player(투수)",
        "player_id": 200001, "date": date, "birth_year": 2008,
        "current_stats": {key: stats[key] for key in ("pit_IP","pit_K","pit_W","pit_H","bat_AB","bat_H","bat_HR","bat_RBI")},
        "previous_seasons": [{"career_year":1,"pit_K":80,"pit_W":5,"bat_HR":4}],
        "profile_record_kind":37, "plain_size":len(plain),
    }
    return bytes(header) + bytes(plain), expected


def word(text, x, y, width=40):
    return {"text":str(text), "x":x, "y":y, "w":width, "h":24}


def line(text, y, words=None):
    return {"text":text, "words":words if words is not None else [word(text, 50, y, max(80, len(text)*12))]}


def score_lines(bad_visitor=False):
    xs = [650 + i*90 for i in range(12)]
    rows = [line("1 2 3 4 5 6 7 8 9 R H E", 300,
                 [word(label,x,300) for x,label in zip(xs,[*range(1,10),"R","H","E"])])]
    for y, values in ((380,[0,1,0,0,2,0,0,0,0,4 if bad_visitor else 3,7,1]),
                      (460,[1,0,2,0,0,1,0,2,"x",6,9,0])):
        rows.append(line(" ".join(map(str,values)), y, [word(v,x,y) for x,v in zip(xs,values)]))
    return rows


def ocr_fixtures():
    base = {"width":2560,"height":1600,"engine_language":"ko","languages":["ko"],
            "max_dimension":10000,"text_angle":0,"source":"authored synthetic OCR grid"}
    result = [line("경기 결과",30), line("2030년 5월 12일",80),
              line("테스트구장 TestHawks VS AwayBears 9회전 6승 3패 0무",140),
              line("관중 27500명",200), *score_lines(), line("승리 투수",1000),
              line("Starter One 4승 2패",1040),line("홈런",1100),
              line("Test Hitter 3,4호",1140)]
    log = [line("타격 기록(홈)",30),line("출전 수비 HR 안타",80),*score_lines(True),
           line("Test Hitter HR HR HR HR",950,[word("Test Hitter",180,950,150),
                *[word("HR",600+i*160,950) for i in range(4)]])]
    columns = ["AB","안타","2B","3B","홈런","타점","득점","삼진","도루","타율"]
    xs = [730 + i*140 for i in range(len(columns))]
    stats = [line("야수 성적(홈)",30),line("BATTING",80),
             line(" ".join(columns),300,[word(c,x,300,65) for c,x in zip(columns,xs)]),
             line("Test Hitter",450,[word("Test",180,450,70),word("Hitter",260,450,90)]),
             line("4 2 0 0 1 2 1 1 0 .321",450,
                  [word(v,x,450,65) for v,x in zip([4,2,0,0,1,2,1,1,0,".321"],xs)])]
    return {
        "game_result_ko.json":{**base,"lines":result},
        "game_result_en.json":{**base,"lines":[line("Synthetic unknown screen",30)]},
        "batting_log_ko.json":{**base,"lines":log},
        "batting_log_ko_2.json":{**base,"lines":log,"synthetic_variant":2},
        "batting_stats_ko.json":{**base,"lines":stats},
    }


def payloads():
    raw, metadata = build()
    encoded = lambda value: (json.dumps(value,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    return {"starplayer-plain.bin":raw,"starplayer-plain.json":encoded(metadata),
            **{f"ocr/{name}":encoded(value) for name,value in ocr_fixtures().items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true")
    args = parser.parse_args()
    for name, data in payloads().items():
        path = FIXTURES / name
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                raise SystemExit(f"Synthetic fixture drift: {name}")
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
    print("Seven synthetic fixtures verified." if args.check else "Seven synthetic fixtures generated from code only.")


if __name__ == "__main__":
    main()
