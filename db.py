import os
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


def check_restaurant_status(restaurant_id: str) -> bool:
    """Restoranın statusunu yoxlayır (Aktiv və ya Sınaq aktivdir)."""
    try:
        restoran = get_restaurant_info(restaurant_id)
        if not restoran:
            return False

        status = str(restoran.get("Status", "")).strip().lower()
        return status in ["aktiv", "sınaq aktivdir", "sinaq aktivdir", "active"]
    except Exception as e:
        print(f"❌ Status yoxlama xətası: {e}")
        return False


def get_restaurant_info(restaurant_id: str):
    """Əvvəlcə 'Restoraunt', tapılmasa 'Müraciətlər' cədvəlindən axtarır."""
    try:
        clean_id = restaurant_id.strip().lower()

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

        # 2. 'Restoraunt' cədvəlində Restoraunt_ID sütunu üzrə axtarış
        formula = f"LOWER({{Restoraunt_ID}}) = '{clean_id}'"
        records = restaurants_table.all(formula=formula)

        if records:
            fields = records[0]["fields"]
            return {"id": records[0]["id"], "source": "Restoraunt", **fields}

        # 3. Tapılmadısa, 'Müraciətlər' cədvəlində axtarış
        muraciet_records = muracietler_table.all(formula=formula)
        if muraciet_records:
            fields = muraciet_records[0]["fields"]
            return {
                "id": muraciet_records[0]["id"],
                "source": "Müraciətlər",
                **fields,
            }

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
        clean_rest_id = restaurant_id.strip().lower()
        rest_name = str(restoran.get("Name", "")).strip().lower()

        all_records = menu_table.all()
        categorized_menu = {}

        for r in all_records:
            fields = r["fields"]

            is_available = fields.get("Is_Available", True)
            if not is_available:
                continue

            linked_restaurants = fields.get("Restoraunt", [])
            linked_str_list = [str(x).strip().lower() for x in linked_restaurants]

            menu_rest_id = str(fields.get("Restoraunt_ID", "")).strip().lower()

            is_match = False

            # Dəqiq uyğunlaşdırma yoxlanışı
            if rest_record_id and rest_record_id.lower() in linked_str_list:
                is_match = True
            elif rest_name and rest_name in linked_str_list:
                is_match = True
            elif clean_rest_id in linked_str_list:
                is_match = True
            elif menu_rest_id and (menu_rest_id == clean_rest_id or menu_rest_id == rest_name):
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
