"""从邮箱本地名里猜「姓 名」的分界。

amy.chen01@x.com 这种带分隔符的好办，split 一下就行。难的是 amychen01@x.com——
中间没有任何标记，得先知道 chen 是个姓，才敢在那儿下刀。

所以这里只做一件事：**找切点**，不判断谁是姓谁是名，也不调换顺序。
amychen → Amy Chen，chenamy → Chen Amy，本地名怎么写就怎么显示。
理由是这个名字要进 From 头，人家自己怎么写的最准，我们没资格替他排。

拿不准就整个原样返回。宁可显示成 Amychen，也不要把 marketing 切成 Mark Eting。
"""

from __future__ import annotations

# 拼音姓 + 常见英文姓。只收敢下刀的，两个字母的（he/yu/ye）故意收得保守，
# 它们太容易在英文单词里撞出假边界。
SURNAMES = {
    # 拼音
    "li", "wang", "zhang", "liu", "chen", "yang", "huang", "zhao", "wu", "zhou",
    "xu", "sun", "ma", "zhu", "hu", "guo", "lin", "luo", "gao", "zheng",
    "liang", "xie", "song", "tang", "deng", "han", "feng", "cao", "peng", "zeng",
    "xiao", "tian", "dong", "yuan", "pan", "cai", "jiang", "du", "cheng", "wei",
    "su", "lu", "ding", "ren", "shen", "yao", "cui", "zhong", "tan", "fan",
    "qian", "yan", "duan", "lei", "hou", "long", "shao", "meng", "qin", "bai",
    "jia", "mao", "hao", "kong", "gong", "kang", "yin", "shi", "xiong", "jin",
    "qiu", "wen", "chang", "wan", "lai", "tong", "fu", "zou", "xia", "hong",
    "ouyang", "situ", "zhuge", "shangguan", "xiahou", "huangfu", "dongfang",
    # 英文 / 其他
    "smith", "johnson", "williams", "brown", "jones", "miller", "davis",
    "wilson", "anderson", "taylor", "thomas", "moore", "jackson", "martin",
    "white", "harris", "clark", "lewis", "walker", "hall", "young", "allen",
    "king", "wright", "scott", "hill", "green", "adams", "baker", "nelson",
    "carter", "mitchell", "roberts", "turner", "phillips", "campbell", "parker",
    "evans", "edwards", "collins", "stewart", "morris", "murphy", "cook",
    "rogers", "morgan", "bell", "bailey", "cooper", "reed", "kelly", "howard",
    "ward", "cox", "richardson", "wood", "watson", "brooks", "bennett", "gray",
    "price", "foster", "ross", "powell", "hughes", "butler", "simmons",
    "barnes", "fisher", "henderson", "coleman", "jenkins", "perry", "russell",
    "bryant", "griffin", "hayes", "myers", "ford", "hamilton", "graham",
    "sanders", "wallace", "west", "cole", "hunt", "marshall", "owens",
    "harrison", "kennedy", "wells", "spencer", "porter", "hunter", "crawford",
    "boyd", "mason", "lee", "kim", "park", "choi", "nguyen", "tran",
    "sato", "tanaka", "suzuki", "yamamoto",
}

# 常见英文名（含中国团队爱用的那批花名：coco / echo / kiki / yoyo…）
GIVEN_NAMES = {
    "alan", "alex", "alice", "amanda", "amy", "andy", "angela", "anna", "anne",
    "april", "ashley", "becky", "bella", "ben", "betty", "bill", "bob", "brian",
    "bruce", "candy", "carol", "cathy", "cecilia", "celia", "charles", "cherry",
    "chris", "cindy", "claire", "coco", "connie", "cora", "daisy", "dan",
    "daniel", "dave", "david", "dean", "diana", "dora", "echo", "eddie",
    "eden", "edward", "elaine", "ella", "ellen", "elsa", "emily", "emma",
    "eric", "erica", "esther", "eva", "evan", "evelyn", "fanny", "fiona",
    "flora", "frank", "gary", "gigi", "gina", "grace", "hannah", "harry",
    "hazel", "heidi", "helen", "henry", "ian", "irene", "iris", "isabel",
    "ivan", "ivy", "jack", "jackie", "jacky", "jacob", "james", "jane",
    "janet", "jason", "jay", "jean", "jeff", "jenny", "jerry", "jessica",
    "jessie", "jill", "jimmy", "joan", "joanna", "joe", "john", "johnny",
    "jordan", "joy", "joyce", "judy", "julia", "julie", "justin", "karen",
    "kate", "kathy", "katie", "kelly", "ken", "kenny", "kevin", "kiki",
    "kris", "kyle", "lance", "larry", "laura", "lena", "leo", "leon",
    "leslie", "lily", "linda", "lisa", "lucas", "lucy", "luke", "lydia",
    "maggie", "mandy", "marco", "maria", "mark", "mary", "matt", "max",
    "may", "megan", "mia", "michael", "michelle", "mike", "molly", "monica",
    "nancy", "naomi", "nathan", "nelly", "nick", "nicole", "nina", "noah",
    "olivia", "oscar", "owen", "paul", "paula", "peggy", "penny", "peter",
    "phoebe", "rachel", "ray", "rebecca", "rex", "richard", "rick", "rita",
    "robert", "robin", "roger", "rose", "roy", "ruby", "ryan", "sally",
    "sam", "samuel", "sandy", "sara", "sarah", "scott", "sean", "selena",
    "selina", "serena", "sharon", "sherry", "shirley", "simon", "sofia",
    "sonia", "sophia", "sophie", "stella", "steve", "steven", "summer",
    "sunny", "susan", "sylvia", "tammy", "tanya", "ted", "teresa", "terry",
    "thomas", "tiffany", "tim", "tina", "tom", "tommy", "tony", "tracy",
    "travis", "vera", "vicky", "victor", "victoria", "vincent", "vivian",
    "wade", "wendy", "will", "william", "winnie", "yoyo", "zoe",
    # 下面这些是拿英文词典跑出来的：不收进来就会被自己的词表切开
    # （christian → Chris Tian，hanna → Han Na）
    "christian", "kristian", "hanna", "lilian", "luna", "maya", "duke",
    "franklin", "susi", "lulu",
}

# 名字里常见的拼音音节。用来兜「姓 + 全拼名」这一类（lixiaoming）。
# 只收真的会出现在名字里的，像 ne / man / ter 这种一律不收——
# 收进来 sunshine 就会被切成 Sun Shine。
GIVEN_SYLLABLES = {
    "bao", "bin", "bing", "bo", "chao", "chen", "cheng", "chun", "cong", "cui",
    "dan", "dong", "duo", "fan", "fang", "fei", "feng", "gang", "guang", "gui",
    "guo", "hai", "han", "hang", "hao", "heng", "hong", "hua", "huan", "hui",
    "ji", "jia", "jian", "jiang", "jiao", "jie", "jin", "jing", "jun", "kai",
    "kang", "ke", "kun", "lan", "lei", "li", "lian", "liang", "lin", "ling",
    "long", "lu", "mei", "meng", "miao", "min", "ming", "na", "nan", "ni",
    "ning", "pei", "peng", "ping", "qi", "qian", "qiang", "qiao", "qin",
    "qing", "qiu", "quan", "qun", "ran", "ren", "rong", "ru", "rui", "run",
    "sen", "shan", "shang", "shen", "sheng", "shi", "shu", "shuai", "shuang",
    "si", "song", "su", "tao", "teng", "tian", "ting", "tong", "wan", "wei",
    "wen", "xi", "xia", "xian", "xiang", "xiao", "xin", "xing", "xiu", "xu",
    "xuan", "xue", "xun", "ya", "yan", "yang", "yao", "ye", "yi", "yin",
    "ying", "yong", "you", "yu", "yuan", "yue", "yun", "ze", "zhan", "zhen",
    "zheng", "zhi", "zhong", "zhou", "zhu", "zhuo", "zi", "zong",
}

# 职能邮箱，本来就不是人名，别去切它。
# 后半段是拿英文词典跑出来的惯犯：拼音读法完全成立（shi+ning、wan+ting），
# 只能挨个拉黑。
ROLE_LOCALS = {
    "info", "sales", "support", "contact", "hello", "service", "team", "admin",
    "marketing", "business", "official", "noreply", "no-reply", "press",
    "media", "help", "office", "mail", "email", "apply", "jobs", "career",
    "careers", "partner", "partners", "partnership", "cooperation", "hr", "pr",
    "bd", "biz", "finance", "billing", "invoice",
    "shining", "wanting", "panting", "mating", "lining", "rented", "sunken",
    "sunray", "sunshine", "hallmark", "dingdong", "sampan", "pandora",
    "sugary", "lichen", "liken", "whitening", "tanning", "panning", "fanning",
}


def _pinyin_name(rest: str) -> bool:
    """rest 能不能读成一个 1~2 个音节的拼音名（xiaoming = xiao + ming）。"""
    if rest in GIVEN_SYLLABLES:
        return True
    # 从长到短切第一个音节，两边都得是音节才算数
    for i in range(len(rest) - 2, 1, -1):
        if rest[:i] in GIVEN_SYLLABLES and rest[i:] in GIVEN_SYLLABLES:
            return True
    return False


def split_name_token(token: str) -> tuple[str, ...]:
    """把连写的本地名切成两段，切不动就原样返回单元素元组。

    amychen    -> ('amy', 'chen')      英文名 + 拼音姓
    chenamy    -> ('chen', 'amy')      顺序保持原样，不替人排姓名
    lixiaoming -> ('li', 'xiaoming')   姓 + 全拼名
    marketing  -> ('marketing',)       切不动
    """
    low = (token or "").lower()
    if len(low) < 4 or not low.isalpha() or low in ROLE_LOCALS:
        return (token,)
    # 整个词本身就是个名字（tanya / franklin），别再往里切
    if low in GIVEN_NAMES or low in SURNAMES:
        return (token,)

    # (把握, 姓有多长)——两个候选都成立时，取姓更长的那个：
    # huangxiaoming 既能读成 huang|xiaoming 也能读成 hu|angxiaoming，前者才对。
    best = None
    for i in range(2, len(low) - 1):
        left, right = low[:i], low[i:]
        if left in SURNAMES and right in GIVEN_NAMES:
            score = (2, len(left))
        elif left in GIVEN_NAMES and right in SURNAMES:
            score = (2, len(right))
        # 姓 + 全拼名。名至少 3 个字母：两个字母的残段（du|ke、ma|ya）
        # 太容易在英文词里撞出假边界，宁可不拆。
        elif left in SURNAMES and len(right) >= 3 and _pinyin_name(right):
            score = (1, len(left))
        else:
            continue
        if best is None or score > best[0]:
            best = (score, left, right)

    return (best[1], best[2]) if best else (token,)
