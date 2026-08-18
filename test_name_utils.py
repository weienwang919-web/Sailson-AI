"""name_utils / name_from_email 的用例表。

这是个靠词表的启发式，改词表很容易顾此失彼（放宽一点 marketing 就被切成
Mark Eting，收紧一点 amychen 又认不出来）。所以把两边的边界都钉在这儿。

    python3 test_name_utils.py
"""

import sys

from mail_blaster_service import name_from_email
from name_utils import split_name_token

CASES = [
    # --- 原有行为：有分隔符的 ---
    ("amy.chen01@x.com", "Amy Chen"),
    ("amy_chen@x.com", "Amy Chen"),
    ("amy-chen@x.com", "Amy Chen"),
    ("john.a.smith@x.com", "John A Smith"),
    ("Amy.Chen@x.com", "Amy Chen"),
    ("amy@x.com", "Amy"),
    ("", ""),
    ("12345@x.com", "12345"),
    # --- 连写的，要拆开 ---
    ("amychen01@x.com", "Amy Chen"),       # 英文名 + 拼音姓
    ("chenamy@x.com", "Chen Amy"),         # 顺序按本地名，不替人排姓名
    ("tonyliu@x.com", "Tony Liu"),
    ("echozhang@x.com", "Echo Zhang"),
    ("lilywang@x.com", "Lily Wang"),
    ("johnsmith@x.com", "John Smith"),
    ("lixiaoming@x.com", "Li Xiaoming"),   # 姓 + 全拼名
    ("wangwei@x.com", "Wang Wei"),
    ("huangxiaoming@x.com", "Huang Xiaoming"),  # 不是 Hu Angxiaoming
    ("amy01chen@x.com", "Amy Chen"),       # 数字也当分隔符
    # --- 不许拆 ---
    ("marketing@x.com", "Marketing"),
    ("business@x.com", "Business"),
    ("support@x.com", "Support"),
    ("noreply@x.com", "Noreply"),
    ("sunshine@x.com", "Sunshine"),        # 不是 Sun Shine
    ("christian@x.com", "Christian"),      # 不是 Chris Tian
    ("tanya@x.com", "Tanya"),              # 整个词本身就是名字
    ("duke@x.com", "Duke"),                # 两个字母的残段不认
    ("shirley@x.com", "Shirley"),
    ("lily@x.com", "Lily"),
    ("hunter@x.com", "Hunter"),
    ("sailson@x.com", "Sailson"),          # 词表覆盖不到就整段留着
]


def main() -> int:
    failed = 0
    for email, want in CASES:
        got = name_from_email(email)
        if got == want:
            print(f"  ok    {email or '(空)'} -> {got!r}")
        else:
            failed += 1
            print(f"  FAIL  {email or '(空)'} -> {got!r}，应该是 {want!r}")

    # split_name_token 直接调用时也得是同一套判断
    assert split_name_token("amychen") == ("amy", "chen")
    assert split_name_token("marketing") == ("marketing",)
    assert split_name_token("") == ("",)
    assert split_name_token("li") == ("li",)

    print(f"\n{len(CASES) - failed}/{len(CASES)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
