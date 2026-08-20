import logging
import os
import threading
from dotenv import load_dotenv
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import (
    check_restaurant_status,
    get_restaurant_info,
    get_restaurant_menu,
    save_order,
)

# --- Flask Web Server (Render Web Service Üçün) ---
app = Flask("")


@app.route("/")
def home():
    return "Bot və Web Server Aktivdir!"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# --------------------------------------------------

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def check_active_subscription(
    restaurant_id: str, update: Update, is_callback: bool = False
) -> bool:
    """Restoranın abunəlik statusunu yoxlayır."""
    if not check_restaurant_status(restaurant_id):
        error_message = (
            "⚠️ **Xidmət Müvəqqəti Dayandırılıb**\n\n"
            "Bu restoranın sınaq müddəti və ya aylıq abunəliyi başa çatmışdır.\n"
            "Sifariş qəbulunu bərpa etmək üçün dəstək xidməti ilə əlaqə saxlayın.\n\n"
            "📞 **Əlaqə:** +994 XX XXX XX XX"
        )
        if is_callback and update.callback_query:
            await update.callback_query.message.reply_text(
                error_message, parse_mode="Markdown"
            )
        elif update.message:
            await update.message.reply_text(error_message, parse_mode="Markdown")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if args:
        restaurant_id = args[0]

        if not await check_active_subscription(restaurant_id, update):
            return

        restoran = get_restaurant_info(restaurant_id)

        if not restoran:
            await update.message.reply_text("❌ Belə bir aktiv restoran tapılmadı.")
            return

        context.user_data["restaurant_id"] = restaurant_id
        context.user_data["cart"] = {}

        rest_name = restoran.get("Name", restaurant_id)

        keyboard = [
            [
                InlineKeyboardButton(
                    "🍽️ Menyunu Göstər", callback_data="show_categories"
                )
            ],
            [InlineKeyboardButton("🛒 Səbətim", callback_data="show_cart")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"Xoş gəldiniz! 🍽️\n\n"
            f"Siz hazırda **{rest_name}** restoranının menyusuna baxırsınız.\n"
            f"Sifariş vermək üçün aşağıdakı düymələrdən istifadə edin:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "⚠️ Xahiş olunur restoranın masasındakı QR kodu oxudaraq "
            "və ya sizə təqdim olunan keçid vasitəsilə bota daxil olun!"
        )


# --- 1-Cİ ADDIM: KATEQORİYALARI GÖSTƏRMƏK ---
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    restaurant_id = context.user_data.get("restaurant_id")

    if not restaurant_id:
        await query.message.reply_text("Xahiş olunur QR kodu yenidən oxudun!")
        return

    if not await check_active_subscription(restaurant_id, update, is_callback=True):
        return

    categorized_menu = get_restaurant_menu(restaurant_id)

    if not categorized_menu:
        await query.message.reply_text("Bu restorana aid mövcud menyu tapılmadı.")
        return

    keyboard = []
    # Yalnız kateqoriya adlarını düymə kimi yığırıq
    for category_name in categorized_menu.keys():
        keyboard.append(
            [InlineKeyboardButton(f"📂 {category_name}", callback_data=f"cat_{category_name}")]
        )

    keyboard.append([InlineKeyboardButton("🛒 Səbətə Keç", callback_data="show_cart")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = "📋 **Zəhmət olmasa kateqoriya seçin:**"
    
    # Əgər callback-dən gəliblərsə mesajı redaktə edirik, yoxsa yeni yazırıq
    if query.message:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")


# --- 2-Cİ ADDIM: SEÇİLƏN KATEQORİYANIN YEMƏKLƏRİNİ GÖSTƏRMƏK ---
async def show_category_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    restaurant_id = context.user_data.get("restaurant_id")
    if not restaurant_id or not await check_active_subscription(restaurant_id, update, is_callback=True):
        return

    selected_cat = query.data.replace("cat_", "")
    categorized_menu = get_restaurant_menu(restaurant_id)
    items = categorized_menu.get(selected_cat, [])

    keyboard = []
    text = f"📂 **{selected_cat}** menyusu:\n\n"

    for item in items:
        text += f"🔹 **{item['name']}** — {item['price']} AZN\n"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"➕ {item['name']} ({item['price']} AZN)",
                    callback_data=f"add_{item['id']}_{item['name']}_{item['price']}",
                )
            ]
        )

    # Geri və Səbət düymələri
    keyboard.append([InlineKeyboardButton("⬅️ Kateqoriyalara Qayıt", callback_data="show_categories")])
    keyboard.append([InlineKeyboardButton("🛒 Səbətə Keç", callback_data="show_cart")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    restaurant_id = context.user_data.get("restaurant_id")

    if restaurant_id and not await check_active_subscription(
        restaurant_id, update, is_callback=True
    ):
        return

    _, item_id, item_name, item_price = query.data.split("_", 3)
    item_price = float(item_price)

    cart = context.user_data.get("cart", {})

    if item_id in cart:
        cart[item_id]["count"] += 1
    else:
        cart[item_id] = {"name": item_name, "price": item_price, "count": 1}

    context.user_data["cart"] = cart
    await query.answer(f"✅ {item_name} səbətə əlavə olundu!", show_alert=False)


async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get("cart", {})

    if not cart:
        await query.message.reply_text("🛒 Səbətiniz hazırda boşdur.")
        return

    text = "🛒 **SİZİN SƏBƏTİNİZ:**\n\n"
    total = 0.0

    for item_id, item in cart.items():
        subtotal = item["price"] * item["count"]
        total += subtotal
        text += f"▪️ {item['name']} x {item['count']} = {subtotal:.2f} AZN\n"

    text += f"\n💰 **Ümumi Məbləğ:** {total:.2f} AZN"

    keyboard = [
        [InlineKeyboardButton("✅ Sifarişi Təsdiqlə", callback_data="checkout")],
        [InlineKeyboardButton("⬅️ Menyuya Qayıt", callback_data="show_categories")],
        [InlineKeyboardButton("🗑️ Səbəti Təmizlə", callback_data="clear_cart")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    restaurant_id = context.user_data.get("restaurant_id")
    if restaurant_id and not await check_active_subscription(
        restaurant_id, update, is_callback=True
    ):
        return

    context.user_data["awaiting_location"] = True
    await query.message.reply_text(
        "📍 Lütfən **Masa nömrənizi** və ya **Çatdırılma ünvanınızı** yazın:"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_location"):
        restaurant_id = context.user_data.get("restaurant_id")

        if restaurant_id and not await check_active_subscription(
            restaurant_id, update
        ):
            return

        address = update.message.text
        cart = context.user_data.get("cart", {})
        user_id = update.effective_user.id

        items_summary_list = []
        total_price = 0.0

        for item_id, item in cart.items():
            subtotal = item["price"] * item["count"]
            total_price += subtotal
            items_summary_list.append(
                f"{item['count']}x {item['name']} ({subtotal:.2f} AZN)"
            )

        items_summary = ", ".join(items_summary_list)

        try:
            save_order(
                restaurant_id=restaurant_id,
                items_summary=items_summary,
                total_price=total_price,
                user_id=user_id,
                order_type="Məkanda",
                address=address,
                phone="-",
            )

            await update.message.reply_text(
                f"🎉 **Sifarişiniz qəbul olundu!**\n\n"
                f"📍 Ünvan/Masa: {address}\n"
                f"💵 Məbləğ: {total_price:.2f} AZN\n\n"
                f"Yeməyiniz tezliklə hazırlanacaq! 👨‍🍳",
                parse_mode="Markdown",
            )

            context.user_data["cart"] = {}
            context.user_data["awaiting_location"] = False

        except Exception as err:
            logging.error("Sifariş xətası: %s", err)
            await update.message.reply_text(
                "❌ Sifariş göndərilərkən xəta baş verdi. Yenidən cəhd edin."
            )


async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["cart"] = {}
    await query.message.reply_text("🗑️ Səbətiniz təmizləndi.")


def main():
    threading.Thread(target=run_web, daemon=True).start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    
    # Yenilənmiş Kateqoriya Handler-ləri
    application.add_handler(
        CallbackQueryHandler(show_categories, pattern="^(show_menu|show_categories)$")
    )
    application.add_handler(
        CallbackQueryHandler(show_category_items, pattern="^cat_")
    )
    
    application.add_handler(CallbackQueryHandler(add_to_cart, pattern="^add_"))
    application.add_handler(
        CallbackQueryHandler(show_cart, pattern="^show_cart$")
    )
    application.add_handler(
        CallbackQueryHandler(clear_cart, pattern="^clear_cart$")
    )
    application.add_handler(
        CallbackQueryHandler(checkout, pattern="^checkout$")
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("🚀 Bot və Web server uğurla işə düşdü...")
    application.run_polling()


if __name__ == "__main__":
    main()
