import os
from pyairtable import Api
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

api = Api(AIRTABLE_API_KEY)

# Airtable cədvəlləri
restaurants_table = api.table(AIRTABLE_BASE_ID, "Restoraunt")
menu_table = api.table(AIRTABLE_BASE_ID, "Menu")
orders_table = api.table(AIRTABLE_BASE_ID, "Orders")


def check_restaurant_status(restaurant_id: str) -> bool:
    """
    Restoranın statusunu yoxlayır.
    'Sınaq aktivdir' və ya 'Ödənişli müştəri' olduqda True qaytarır.
    """
    try:
        restoran = get_restaurant_info(restaurant_id)
        if not restoran:
            return False

        status = restoran.get('Status', '')
        allowed_statuses = ["Sınaq aktivdir", "Ödənişli müştəri"]
        return status in allowed_statuses
    except Exception as e:
        print(f"❌ Status yoxlama xətası: {e}")
        return False


def get_restaurant_info(restaurant_id: str):
    """Restoran məlumatlarını ID-yə görə gətirir"""
    try:
        if restaurant_id.startswith("rec"):
            record = restaurants_table.get(restaurant_id)
            fields = record['fields']
            if not fields.get('Is_Active', True):
                return None
            return {"id": record['id'], **fields}

        formula = f"{{Restoraunt_ID}} = '{restaurant_id}'"
        records = restaurants_table.all(formula=formula)

        if records:
            fields = records[0]['fields']
            if not fields.get('Is_Active', True):
                return None
            return {"id": records[0]['id'], **fields}

    except Exception as e:
        print(f"❌ Restoran xətası: {e}")
    return None


def get_restaurant_menu(restaurant_id: str):
    """Restorana aid menyunu kateqoriyalara bölünmüş şəkildə gətirir"""
    try:
        restoran = get_restaurant_info(restaurant_id)
        if not restoran:
            return {}

        # Əgər restoranın sınaq müddəti bitibsə və ya imtina edibsə menyunu vermirik
        status = restoran.get('Status', '')
        if status not in ["Sınaq aktivdir", "Ödənişli müştəri"]:
            print(f"⚠️ Restoranın statusu aktiv deyil: {status}")
            return {}

        rest_record_id = restoran['id']
        all_records = menu_table.all()

        categorized_menu = {}
        for r in all_records:
            fields = r['fields']

            is_available = fields.get('Is_Available', True)
            if not is_available:
                continue

            linked_restaurants = fields.get('Restoraunt', [])

            if rest_record_id in linked_restaurants:
                category = fields.get('Category', 'Digər')

                item = {
                    "id": r['id'],
                    "name": fields.get('Name', 'Adsız Məhsul'),
                    "price": fields.get('Price', 0.0),
                    "image_url": fields.get('Image', [{}])[0].get('url') if fields.get('Image') else None
                }

                if category not in categorized_menu:
                    categorized_menu[category] = []
                categorized_menu[category].append(item)

        return categorized_menu
    except Exception as e:
        print(f"❌ Menyu xətası: {e}")
        return {}


def save_order(restaurant_id: str, items_summary: str, total_price: float, user_id: int, order_type: str = "Məkanda",
               address: str = "-", phone: str = "-"):
    """Səbətdəki bütün sifarişləri Orders cədvəlinə əlavə edir"""
    try:
        restoran = get_restaurant_info(restaurant_id)
        rest_record_id = restoran.get('id') if restoran else None

        # Price rəqəm tipinə tam çevrilir
        cleaned_price = round(float(total_price), 2)

        payload = {
            "Customer_Name": f"User ID: {user_id}",
            "Order_Details": items_summary,
            "Total_Price": cleaned_price,
            "Order_Type": order_type,
            "Address": address,
            "Phone": str(phone),
            "Status": "Yeni"
        }

        if rest_record_id:
            payload["Restoraunt"] = [rest_record_id]

        new_order = orders_table.create(payload)

        return {
            "items_summary": items_summary,
            "total_price": cleaned_price,
            "order_id": new_order['id'],
            "order_type": order_type,
            "address": address,
            "phone": phone
        }
    except Exception as e:
        print(f"❌ AIRTABLE SAVE XƏTASI: {e}")
        raise e
