"""素材提交逐行附件 —— 端到端验证 B/C/D/E。

用一次性库跑真实代码路径：建 Excel → 导入 → 挂附件 → 发信（本地 SMTP sink）。
不碰生产库，不发真信。
"""
import io
import os
import sys
import json
import email

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mail_blaster_service as mb
import database as db
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from PIL import Image as PILImage

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {label}: {got!r}" + ("" if ok else f"  (期望 {want!r})"))
    if not ok:
        FAILS.append(label)
    return ok


def png_bytes(color):
    buf = io.BytesIO()
    PILImage.new("RGB", (40, 30), color).save(buf, format="PNG")
    return buf.getvalue()


def build_xlsx():
    """四种形态：有图+邮箱 / 无图+邮箱 / 无图+无邮箱 / 有图+无邮箱"""
    wb = Workbook()
    ws = wb.active
    ws.append(["名称", "编号", "收件邮箱"])
    ws.append(["有图有邮箱", "M001", "a@example.com"])
    ws.append(["无图有邮箱", "M002", "b@example.com"])
    ws.append(["无图无邮箱", "M003", ""])
    ws.append(["有图无邮箱", "M004", ""])
    for row, color in ((2, "red"), (5, "blue")):
        img = XLImage(io.BytesIO(png_bytes(color)))
        img.anchor = f"A{row}"
        ws.add_image(img)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_account():
    """建一个 list_accounts(only_sendable=True) 认得的发件账号。"""
    return db.execute_and_fetch_id("""
        INSERT INTO mb_sender_accounts
            (email, provider, auth_mode, encrypted_password, display_name, signature_name,
             smtp_host, smtp_port, purpose, status, enabled, daily_limit)
        VALUES (%s,'custom','password','enc','测试发件人','测试签名',
                'localhost',1025,'material','ready',TRUE,NULL)
        RETURNING id
    """, ("sender@example.com",))


# --------------------------------------------------------------------------- #
print("\n=== B 导入：四种形态 ===")
mb.ensure_schema()
db.execute("DELETE FROM mb_jobs")
db.execute("DELETE FROM mb_history")
db.execute("DELETE FROM mb_sender_accounts")
account_id = make_account()

parsed = mb.parse_material_xlsx(build_xlsx())
check("解析出的行数", len(parsed["rows"]), 4)
check("notices 条数（无图的两行）", len(parsed["notices"]), 2)
check("errors 里不再有「没有图片」",
      any("没有图片" in e for e in parsed["errors"]), False)

payload = mb.create_job_from_excel(file_bytes=build_xlsx(), run_ocr=False)
job_id = payload["job"]["id"]
x = payload["excel"]
check("入队行数（有邮箱的两行）", x["imported"], 2)
check("no_image 计数", x["no_image"], 1)
check("跳过行数（无邮箱的两行）", len(x["skipped"]), 2)
# parse 阶段两行无图，但其中一行缺邮箱被丢了 —— 报告里只该提真正入队的那一行，
# 否则数字和 no_image 对不上，用户会去表里找不存在的行
check("excel.notices 只留入队的那行", len(x["notices"]), 1)
check("notices 与 no_image 一致", len(x["notices"]) == x["no_image"], True)
check("needs_ocr（关了 OCR）", x["needs_ocr"], False)

items = payload["items"]
check("每个 item 都带 attachments 键", all("attachments" in i for i in items), True)
with_img = [i for i in items if i["image_id"]]
without_img = [i for i in items if not i["image_id"]]
check("有图的行数", len(with_img), 1)
check("无图的行数（这就是以前被丢掉的）", len(without_img), 1)

# --------------------------------------------------------------------------- #
print("\n=== C 绑定 + 未变行零写入 ===")
pdf = b"%PDF-1.4\n% fake pdf for test\n"
att1 = mb.store_attachment(pdf, "报价单.pdf")
att2 = mb.store_attachment(b"hello attachment two", "second.txt")
img_item, noimg_item = with_img[0], without_img[0]

base_items = [{"id": i["id"], "sender_account_id": account_id,
               "from_display": "", "vars": i["vars"]} for i in items]


def payload_with(attach_for):
    out = []
    for it in base_items:
        row = dict(it)
        row["attachments"] = attach_for.get(it["id"], [])
        out.append(row)
    return {"items": out}


# 无图行挂 1 个 PDF，有图行挂 2 个
mb.sync_job(job_id, payload_with({
    noimg_item["id"]: [{"id": att1["id"], "filename": "报价单.pdf"}],
    img_item["id"]: [{"id": att1["id"], "filename": "报价单.pdf"},
                     {"id": att2["id"], "filename": "second.txt"}],
}))
after = mb.load_job(job_id)
by_id = {i["id"]: i for i in after["items"]}
check("无图行绑定了 1 个附件", len(by_id[noimg_item["id"]]["attachments"]), 1)
check("有图行绑定了 2 个附件", len(by_id[img_item["id"]]["attachments"]), 2)
check("文件名保住了中文", by_id[noimg_item["id"]]["attachments"][0]["filename"], "报价单.pdf")
check("job 级 attachments 仍为空（素材不用它）", after["attachments"], [])

# 同样的 body 再来一次 —— 应该一条写都不发
writes = {"n": 0}
_real_execute = db.execute


def counting_execute(sql, *a, **k):
    if sql.strip().split()[0].upper() in ("INSERT", "UPDATE", "DELETE"):
        if "mb_item_attachments" in sql:
            writes["n"] += 1
    return _real_execute(sql, *a, **k)


db.execute = counting_execute
mb.sync_job(job_id, payload_with({
    noimg_item["id"]: [{"id": att1["id"], "filename": "报价单.pdf"}],
    img_item["id"]: [{"id": att1["id"], "filename": "报价单.pdf"},
                     {"id": att2["id"], "filename": "second.txt"}],
}))
db.execute = _real_execute
check("重复提交同样的绑定 → 附件表零写入", writes["n"], 0)

# 超限
try:
    mb.sync_job(job_id, payload_with({
        noimg_item["id"]: [{"id": att1["id"], "filename": f"f{i}.pdf"} for i in range(9)]}))
    check("超过每行上限应该抛错", "没抛", "抛 ValueError")
except ValueError as e:
    check("超过每行上限抛 ValueError", "最多带" in str(e), True)

# --------------------------------------------------------------------------- #
print("\n=== D 发送矩阵（本地 SMTP sink，抓 MIME） ===")
SENT = {}


class FakeSMTP:
    last_data_response = (250, b"2.0.0 Ok: queued as FAKE")

    def sendmail(self, from_addr, to_addrs, msg, *a, **k):
        SENT[to_addrs[0] if isinstance(to_addrs, list) else to_addrs] = msg
        return {}

    def quit(self):
        pass


mb._open_smtp_recording = lambda account: FakeSMTP()
mb.MIN_GAP_SECONDS = mb.MAX_GAP_SECONDS = 0
mb.OUTREACH_MIN_GAP_SECONDS = mb.OUTREACH_MAX_GAP_SECONDS = 0


def mime_of(recipient):
    return email.message_from_string(SENT[recipient])


summary = mb.run_job(job_id)
print("  run_job:", summary)
after = mb.load_job(job_id)
by_id = {i["id"]: i for i in after["items"]}

m_img = mime_of("a@example.com")
m_noimg = mime_of("b@example.com")
check("有图+2附件 → 顶层 multipart/mixed", m_img.get_content_type(), "multipart/mixed")
check("有图那封里有内联图", any(p.get_content_type().startswith("image/")
                              for p in m_img.walk()), True)
check("无图+1附件 → 顶层 multipart/mixed", m_noimg.get_content_type(), "multipart/mixed")
check("无图那封没有内联图", any(p.get_content_type().startswith("image/")
                              for p in m_noimg.walk()), False)
check("无图那封的正文里没有 <img",
      "<img" in m_noimg.get_payload(0).get_payload(1).get_payload(decode=True).decode(), False)

names = [p.get_filename() for p in m_noimg.walk()
         if p.get_content_disposition() == "attachment"]
check("中文附件名解出来是原名", names, ["报价单.pdf"])
check("两行都 sent",
      [by_id[img_item["id"]]["status"], by_id[noimg_item["id"]]["status"]],
      ["sent", "sent"])

# --------------------------------------------------------------------------- #
print("\n=== D2 既没图也没附件 → 可恢复的 skipped ===")
# 无图行也该进发信记录：上面那两封发出去了，重导同一个 Excel 必须被去重拦住
hist = db.query_all("SELECT recipient, material_id FROM mb_history ORDER BY recipient")
check("两封都记进了 mb_history（含无图那封）",
      [(h["recipient"], h["material_id"]) for h in hist],
      [("a@example.com", "M001"), ("b@example.com", "M002")])
try:
    mb.create_job_from_excel(file_bytes=build_xlsx(), run_ocr=False)
    check("重导同一个 Excel 应该被去重拦住", "没拦住", "抛 ValueError")
except ValueError as e:
    check("重导同一个 Excel 被去重拦住", "已有发送记录" in str(e), True)

db.execute("DELETE FROM mb_history")   # 清掉，下面要重新用同一批数据
payload2 = mb.create_job_from_excel(file_bytes=build_xlsx(), run_ocr=False)
job2 = payload2["job"]["id"]
items2 = payload2["items"]
noimg2 = [i for i in items2 if not i["image_id"]][0]
mb.sync_job(job2, {"items": [{"id": i["id"], "sender_account_id": account_id,
                              "from_display": "", "vars": i["vars"]} for i in items2]})
mb.run_job(job2)
row = {i["id"]: i for i in mb.load_job(job2)["items"]}[noimg2["id"]]
check("无图无附件 → skipped（不是 failed）", row["status"], "skipped")
check("error 带 📎 可恢复前缀", row["error"].startswith("📎"), True)

# 补上附件再跑一次 —— 这就是「捡不回来」那个坑的回归
mb.sync_job(job2, {"items": [{"id": noimg2["id"], "sender_account_id": account_id,
                              "from_display": "", "vars": noimg2["vars"],
                              "attachments": [{"id": att1["id"], "filename": "补的.pdf"}]}]})
mb.run_job(job2)
row = {i["id"]: i for i in mb.load_job(job2)["items"]}[noimg2["id"]]
check("补上附件重跑 → 捡回来并发出去", row["status"], "sent")

# --------------------------------------------------------------------------- #
print("\n=== E 建联回归：job 级附件不受影响 ===")
db.execute("UPDATE mb_sender_accounts SET purpose = 'both' WHERE id = %s", (account_id,))
out = mb.create_outreach_job(
    sender_account_id=account_id,
    rows=[{"email": "kol@example.com", "vars": {"name": "达人"}}],
    subject_tpl="hi", body_tpl="body", signature_tpl="sig",
    attachments=[{"id": att2["id"], "filename": "second.txt"}])
job3 = out["job"]["id"]
state3 = mb.load_job(job3)
check("建联 job 级附件还在", len(state3["attachments"]), 1)
check("建联的 item 不带行级附件", state3["items"][0]["attachments"], [])
prev3 = mb.build_previews(job3)
check("建联预览用的是 job 级附件", len(prev3[0]["attachments"]), 1)
# 只传模板不传 items（templatePayload 的形状）不能碰行级附件
mb.sync_job(job3, {"subject_tpl": "changed"})
check("只改模板不影响建联附件", len(mb.load_job(job3)["attachments"]), 1)

prev_material = mb.build_previews(job_id)
check("素材预览用的是行级附件",
      sorted(len(p.get("attachments", [])) for p in prev_material), [1, 2])

print("\n=== G 代码审查回归 ===")
db.execute("DELETE FROM mb_history")   # 又要重用同一批数据，先清去重记录
# G1：📎 行的「发这一封」按钮。它走 /items/<id>/resend，而用户是在发送**之后**
# 才补的附件 —— 附件在客户端 Map 里，不随 resend 一起落库的话 worker 查不到，
# 按钮就永远空转。修法是 resend 也接受 items 并先 sync_job。
payload3 = mb.create_job_from_excel(file_bytes=build_xlsx(), run_ocr=False)
job3m = payload3["job"]["id"]
items3 = payload3["items"]
noimg3 = [i for i in items3 if not i["image_id"]][0]
mb.sync_job(job3m, {"items": [{"id": i["id"], "sender_account_id": account_id,
                               "from_display": "", "vars": i["vars"]} for i in items3]})
mb.run_job(job3m)
check("先跑一轮，无图行被跳过",
      {i["id"]: i for i in mb.load_job(job3m)["items"]}[noimg3["id"]]["status"], "skipped")

# 模拟按钮：resend 路由现在会先 sync_job(items)，再交给 worker 发这一封
mb.sync_job(job3m, {"items": [{"id": noimg3["id"], "sender_account_id": account_id,
                               "from_display": "", "vars": noimg3["vars"],
                               "attachments": [{"id": att1["id"], "filename": "补的.pdf"}]}]})
outcome = mb.send_item(job3m, noimg3["id"])
check("补附件后单封重发 → 真的发出去了（不是又跳过）", outcome, "sent")

# G2：内容预检要排除已 sent 的行，否则「发过一半再补发」时恒通过
state3m = mb.load_job(job3m)
todo = [i for i in state3m["items"] if i["status"] != "sent"]
check("这批已全部发完，待发为空", todo, [])
sent_rows = [i for i in state3m["items"] if i["status"] == "sent"]
check("若把已发行算进预检，会误判为「有可发的行」（这就是要排除的原因）",
      any(i["sender_account_id"] and (i["image_id"] or i["attachments"]) for i in sent_rows),
      True)

# G3：sync_item_attachments 不能碰已 sent 的行
before = len({i["id"]: i for i in state3m["items"]}[noimg3["id"]]["attachments"])
mb.sync_item_attachments(job3m, {noimg3["id"]: []})
after_n = len({i["id"]: i for i in mb.load_job(job3m)["items"]}[noimg3["id"]]["attachments"])
check("已发出的行的附件不会被改写", (before, after_n), (1, 1))

# --------------------------------------------------------------------------- #
print("\n" + "=" * 60)
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过")
