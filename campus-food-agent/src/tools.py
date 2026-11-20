"""餐廳查詢工具 find_restaurant。

資料來源是 data/restaurants.csv，一間店一行。
這個檔案只負責「依條件篩選」，不呼叫 LLM。
"""
import csv
import os

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "restaurants.csv")
DEFAULT_DISHES_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "data", "dishes.csv")
MAX_RESULTS = 5   
MAX_DISHES = 3   
VEG_DISH_LABELS = ("全素", "奶素", "蛋奶素", "鍋邊素", "五辛素", "素食")

VEG_MODES = ("vegetarian", "mixed")  
REQUIRED_COLUMNS = {"店名", "類型", "賣葷食", "素食選項", "素食類型", "最低價", "最高價", "位置", "備註"}


def load_restaurants(path=None):
    """讀取 CSV，回傳每間店一個 dict 的 list。檔案不存在時會丟 FileNotFoundError。"""
    path = path or os.environ.get("RESTAURANT_CSV", DEFAULT_CSV)
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_dishes(path=None):
    """讀取餐點資料，回傳 {店名: [餐點, ...]}。餐點檔不存在就當作沒有餐點資料（回傳空的）。"""
    path = path or os.environ.get("DISHES_CSV", DEFAULT_DISHES_CSV)
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return {}
    dishes = {}
    for row in rows:
        dishes.setdefault(row["店名"], []).append(
            {"餐點": row["餐點"], "價格": _to_int(row["價格"]), "素食": row["素食"]})
    return dishes


def _to_int(text):
    """價格欄位轉成整數，空白或不是數字就回傳 None。"""
    try:
        return int(str(text).strip())
    except (ValueError, TypeError):
        return None


def _shop_info(row):
    """整理成要回傳給 LLM 的欄位。"""
    return {
        "店名": row["店名"],
        "類型": row["類型"],
        "賣葷食": row["賣葷食"],
        "素食選項": row["素食選項"],
        "素食類型": row["素食類型"],
        "最低價": _to_int(row["最低價"]),
        "最高價": _to_int(row["最高價"]),
        "位置": row["位置"],
        "備註": row["備註"],
    }


def find_restaurant(category=None, veg=None, max_price=None, keyword=None):
    """依條件篩選餐廳。四個參數都可以不填。

    category  : 想吃的類型，例如「麵」「飯」「火鍋」。只要店的「類型」欄有這個字就算符合。
    veg       : "vegetarian"＝吃素，素食選項要是「有」；
                "mixed"＝葷素同行，素食選項是「有」而且賣葷食是「是」。
                素食選項或賣葷食是「待確認」的店一律不算符合。
    max_price : 預算上限（元）。
    keyword   : 餐點名稱關鍵字，例如「餛飩」「咖哩」。只會搜尋有餐點資料的店。

    餐點層級的驗證（有餐點資料的店）：
      - max_price：要有一道餐點的價格 <= 預算，否則這間店不算符合。
      - veg：要有一道餐點標示為素食（全素、奶素、蛋奶素、鍋邊素、五辛素），
             而且價格也符合預算。
      - keyword：餐點名稱要包含關鍵字，同時也要符合上面的素食和預算。
    沒有餐點資料的店，只能用店家層級的欄位判斷（類型、素食選項、最低價），
    結果的 verification 會是 shop_only，提醒使用者到現場確認。

    回傳 dict，status 有三種：
      ok       : 有結果（mode=search）或沒給條件而回傳各類型清單（mode=browse）
      no_match : 有給條件但沒有店符合
      error    : 資料檔讀不到、參數不合法等
    每間店的 verification：
      dish      : 有餐點符合所有餐點層級的條件（見 matched_dishes）
      shop_only : 只確認到店家層級，餐點層級沒有驗證
      n/a       : 沒有餐點層級的條件（只給了類型）
    """
    
    if veg in ("", "none"):
        veg = None
    if veg is not None and veg not in VEG_MODES:
        return {"status": "error", "message": f"veg 只能是 {VEG_MODES}，收到 {veg!r}"}

    if max_price in ("", None):
        max_price = None
    else:
        max_price = _to_int(max_price)
        if max_price is None or max_price <= 0:
            return {"status": "error", "message": "max_price 要是大於 0 的整數（元）"}

    if isinstance(category, str):
        category = category.strip() or None
    if keyword is not None and not isinstance(keyword, str):
        return {"status": "error", "message": "keyword 要是文字"}
    keyword = (keyword or "").strip() or None

    try:
        rows = load_restaurants()
    except FileNotFoundError:
        return {"status": "error", "message": "找不到餐廳資料檔 restaurants.csv"}
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        return {"status": "error", "message": f"餐廳資料檔讀取失敗：{e}"}
    if not rows or not REQUIRED_COLUMNS.issubset(rows[0].keys()):
        return {"status": "error", "message": "餐廳資料檔是空的，或欄位不完整"}
    try:
        dishes_by_shop = load_dishes()
    except (OSError, UnicodeDecodeError, csv.Error, KeyError) as e:
        return {"status": "error", "message": f"餐點資料檔讀取失敗：{e}"}

    
    if category is None and veg is None and max_price is None and keyword is None:
        options = {}
        for row in rows:
            for tag in row["類型"].split("、"):
                tag = tag.strip()
                if tag:
                    options.setdefault(tag, []).append(row["店名"])
        return {"status": "ok", "mode": "browse", "shop_count": len(rows), "options": options}

    results = []
    unsearched = 0  
    for row in rows:
        if category and category not in row["類型"]:
            continue
        if veg == "vegetarian" and row["素食選項"] != "有":
            continue
        if veg == "mixed" and not (row["素食選項"] == "有" and row["賣葷食"] == "是"):
            continue

        dishes = dishes_by_shop.get(row["店名"], [])
        matched = []
        verification = "n/a"

        if keyword and not dishes:
            unsearched += 1
            continue

        if dishes:
            price_ok = [d for d in dishes
                        if max_price is None or (d["價格"] is not None and d["價格"] <= max_price)]
            if max_price is not None and not price_ok:
                continue
            matched = [d for d in price_ok
                       if (not keyword or keyword in d["餐點"])
                       and (not veg or d["素食"] in VEG_DISH_LABELS)]
            if keyword and not matched:
                continue
            if veg or max_price is not None or keyword:
                verification = "dish" if matched else "shop_only"  
        else:
            if max_price is not None:
                low = _to_int(row["最低價"])
                if low is None or low > max_price:
                    continue
            if veg or max_price is not None:
                verification = "shop_only"

        info = _shop_info(row)
        info["verification"] = verification
        info["matched_dishes"] = matched[:MAX_DISHES]
        if verification == "shop_only":
            info["note"] = "只確認到店家層級，餐點層級還沒驗證，請到現場確認"
        results.append(info)

    results = sorted(results, key=lambda r: r["verification"] != "dish")

    conditions = {"category": category, "veg": veg, "max_price": max_price, "keyword": keyword}
    if not results:
        out = {"status": "no_match", "conditions": conditions,
               "message": "資料表裡沒有符合這些條件的店"}
        if keyword:
            out["unsearched_shops"] = unsearched
        return out
    out = {"status": "ok", "mode": "search", "conditions": conditions,
           "total_matches": len(results), "results": results[:MAX_RESULTS]}
    if keyword:
        out["unsearched_shops"] = unsearched  
    return out
