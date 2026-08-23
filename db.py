import os
import re
from dotenv import load_dotenv
from pyairtable import Api

load_dotenv()

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

api = Api(AIRTABLE_API_KEY)

# Airtable cədvəlləri
restaurants_table = api.table(AIRTABLE_BASE_ID, "Restoraunt")
muracietler_table = api.table(AIRTABLE_BASE_ID, "Müraciətlər")
menu_table = api.table(AIRTABLE_BASE_ID, "Menu")
orders_table = api.table(AIRTABLE_BASE_ID, "Orders")


def normalize_str(val: str) -> str:
    """Mətndəki bütün xüsusi simvolları və boşluqları təmizləyərək müqayisəyə hazır edir."""
    if not val:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(val).lower())


def check_restaurant_status(restaurant_id: str) -> bool:
    """Restoranın statusunu yoxlayır (Aktiv və ya Sınaq aktivdir)."""
    try:
        restoran = get_restaurant_info(restaurant_id)
        if not restoran:
            return False

        status = str(restoran.get("Status", "")).strip().lower()

        if not status or any(w in status for w in ["aktiv", "active", "sinaq", "sınaq"]):
            return True

        return False
    except Exception as e:
        print(f"❌ Status yoxlama xətası: {e}")
        return True


def get_restaurant_info(restaurant_id: str):
    """Əvvəlcə 'Restoraunt', tapılmasa 'Müraciətlər' cədvəlindən axtarır."""
    try:
        raw_id = restaurant_id.strip()
        clean_id = raw_id.lower()
        norm_id = normalize_str(raw_id)

        # 1. Record ID vasitəsilə birbaşa axtarış (rec ilə başlayırsa)
        if clean_id.startswith("rec"):
            try:
                record = restaurants_table.get(clean_id)
                return {"id": record["id"], "source": "Restoraunt", **record["fields"]}
            except Exception:
                try:
                    record = muracietler_table.get(clean_id)
                    return {
                        "id": record["id"],
                        "source": "Müraciətlər",
                        **record["fields"],
                    }
                except Exception:
                    pass

        # 2. Formula üzrə sınaq axtarışı (Restoraunt cədvəli)
        formula = f"LOWER({{Restoraunt_ID}}) = '{clean_id}'"
        records = restaurants_table.all(formula=formula)

        if records:
            fields = records[0]["fields"]
            return {"id": records[0]["id"], "source": "Restoraunt", **fields}

        # 3. Python tərəfində tam normalized axtarış (Restoraunt cədvəli)
        all_rest = restaurants_table.all()
        for r in all_rest:
            f = r["fields"]
            r_id = normalize_str(f.get("Restoraunt_ID", ""))
            r_name = normalize_str(f.get("Name", ""))
            if norm_id in [r_id, r_name] or r_id in norm_id:
                return {"id": r["id"], "source": "Restoraunt", **f}

        # 4. 'Müraciətlər' cədvəlində axtarış
        all_muraciet = muracietler_table.all()
        for r in all_muraciet:
            f = r["fields"]
            r_id = normalize_str(f.get("Restoraunt_ID", ""))
            r_name = normalize_str(f.get("Name", ""))
            if norm_id in [r_id, r_name] or r_id in norm_id:
                return {"id": r["id"], "source": "Müraciətlər", **f}

    except Exception as e:
        print(f"❌ Restoran axtarış xətası: {e}")
    return None


def get_restaurant_admin_chat_id(restaurant_id: str):
    """Restoranın Admin_Chat_ID-sini gətirir."""
    try:
        restoran = get_restaurant_info(restaurant_id)
        if restoran:
            return restoran.get("Admin_Chat_ID")
    except Exception as e:
        print(f"❌ Admin Chat ID alma xətası: {e}")
    return None


def get_restaurant_menu(restaurant_id: str):
    """Yalnız sorğu edilən restorana aid menyu elementlərini gətirir."""
    try:
        restoran = get_restaurant_info(restaurant_id)
        if not restoran:
            return {}

        if not check_restaurant_status(restaurant_id):
            print("⚠️ Restoranın statusu aktiv deyil.")
            return {}

        rest_record_id = restoran.get("id", "")
        norm_rest_id = normalize_str(restaurant_id)
        norm_rest_name = normalize_str(restoran.get("Name", ""))

        all_records = menu_table.all()
        categorized_menu = {}

        for r in all_records:
            fields = r["fields"]

            is_available = fields.get("Is_Available", True)
            if not is_available:
                continue

            linked_restaurants = fields.get("Restoraunt", [])
            linked_str_list = [str(x).strip().lower() for x in linked_restaurants]
            linked_norm_list = [normalize_str(x) for x in linked_restaurants]

            menu_rest_id = normalize_str(fields.get("Restoraunt_ID", ""))

            is_match = False

            # Dəqiq və normalized uyğunlaşdırma yoxlanışı
            if rest_record_id and rest_record_id.lower() in linked_str_list:
                is_match = True
            elif norm_rest_name and norm_rest_name in linked_norm_list:
                is_match = True
            elif norm_rest_id and norm_rest_id in linked_norm_list:
                is_match = True
            elif menu_rest_id and (menu_rest_id == norm_rest_id or menu_rest_id == norm_rest_name):
                is_match = True

            if is_match:
                category = fields.get("Category", "Digər")

                item = {
                    "id": r["id"],
                    "name": fields.get("Name", "Adsız Məhsul"),
                    "price": fields.get("Price", 0.0),
                    "image_url": (
                        fields.get("Image", [{}])[0].get("url")
                        if fields.get("Image")
                        else None
                    ),
                }

                if category not in categorized_menu:
                    categorized_menu[category] = []
                categorized_menu[category].append(item)

        return categorized_menu
    except Exception as e:
        print(f"❌ Menyu xətası: {e}")
        return {}


def save_order(
    restaurant_id: str,
    items_summary: str,
    total_price: float,
    user_id: int,
    order_type: str = "Məkanda",
    address: str = "-",
    phone: str = "-",
):
    """Sifarişi Orders cədvəlinə yazır."""
    try:
        restoran = get_restaurant_info(restaurant_id)
        rest_record_id = restoran.get("id") if restoran else None

        cleaned_price = round(float(total_price), 2)

        payload = {
            "Customer_Name": f"User ID: {user_id}",
            "Order_Details": items_summary,
            "Total_Price": cleaned_price,
            "Order_Type": order_type,
            "Address": address,
            "Phone": str(phone),
            "Status": "Yeni",
        }

        if rest_record_id:
            payload["Restoraunt"] = [rest_record_id]

        new_order = orders_table.create(payload)

        return {
            "items_summary": items_summary,
            "total_price": cleaned_price,
            "order_id": new_order["id"],
            "order_type": order_type,
            "address": address,
            "phone": phone,
        }
    except Exception as e:
        print(f"❌ AIRTABLE SAVE XƏTASI: {e}")
        raise e
