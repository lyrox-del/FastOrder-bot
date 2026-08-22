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
    ConversationHandler,
    MessageHandler,
    filters,
)

from db import (
    check_restaurant_status,
    get_restaurant_admin_chat_id,
    get_restaurant_info,
    get_restaurant_menu,
    save_order,
)

# State dəyişənləri (ConversationHandler üçün)
CHOOSING_TYPE, GET_TABLE, GET_ADDRESS, GET_PHONE = range(4)

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
        if "cart" not in context.user_data:
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
    for category_name in categorized_menu.keys():
        keyboard.append(
            [InlineKeyboardButton(f"📂 {category_name}", callback_data=f"cat_{category_name}")]
        )

    keyboard.append([InlineKeyboardButton("🛒 Səbətə Keç", callback_data="show_cart")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = "📋 **Zəhmət olmasa kateqoriya seçin:**"

    if query.message:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")


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


# --- 1. SƏBƏT VƏ ƏDƏD AZALTMA / SİLMƏ MƏNTİQİ ---
async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get("cart", {})

    if not cart:
        text = "🛒 Səbətiniz hazırda boşdur."
        keyboard = [[InlineKeyboardButton("🍽️ Menyuya Qayıt", callback_data="show_categories")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = "🛒 **SİZİN SƏBƏTİNİZ:**\n\n"
    total = 0.0
    keyboard = []

    for item_id, item in cart.items():
        subtotal = item["price"] * item["count"]
        total += subtotal
        text += f"▪️ {item['name']} x {item['count']} = {subtotal:.2f} AZN\n"
        
        # Məhsulun altından -1 və Sil düymələri
        keyboard.append([
            InlineKeyboardButton(f"➖ 1 ədəd çıxar", callback_data=f"sub_{item_id}"),
            InlineKeyboardButton(f"❌ {item['name']} Sil", callback_data=f"del_{item_id}")
        ])

    text += f"\n💰 **Ümumi Məbləğ:** {total:.2f} AZN"

    keyboard.append([InlineKeyboardButton("✅ Sifarişi Təsdiqlə", callback_data="start_checkout")])
    keyboard.append([InlineKeyboardButton("⬅️ Menyuya Qayıt", callback_data="show_categories")])
    keyboard.append([InlineKeyboardButton("🗑️ Səbəti Təmizlə", callback_data="clear_cart")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def handle_cart_item_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    cart = context.user_data.get("cart", {})

    if data.startswith("sub_"):
        item_id = data.replace("sub_", "")
        if item_id in cart:
            cart[item_id]["count"] -= 1
            if cart[item_id]["count"] <= 0:
                del cart[item_id]
    elif data.startswith("del_"):
        item_id = data.replace("del_", "")
        if item_id in cart:
            del cart[item_id]

    context.user_data["cart"] = cart
    await show_cart(update, context)


async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["cart"] = {}
    await query.edit_message_text("🗑️ Səbətiniz təmizləndi.")


# --- 2 & 3. DİNAMİK SEÇİM (MASADA / ÇATDIRILMA) VƏ ADDIMLAR ---
async def start_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    restaurant_id = context.user_data.get("restaurant_id")
    if restaurant_id and not await check_active_subscription(
        restaurant_id, update, is_callback=True
    ):
        return ConversationHandler.END

    cart = context.user_data.get("cart", {})
    if not cart:
        await query.message.reply_text("Səbətiniz boşdur!")
        return ConversationHandler.END

    keyboard = [
        [
            InlineKeyboardButton("🪑 Masada", callback_data="type_table"),
            InlineKeyboardButton("🛵 Çatdırılma", callback_data="type_delivery"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("Lütfən sifariş növünü seçin:", reply_markup=reply_markup)
    return CHOOSING_TYPE


async def handle_order_type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "type_table":
        context.user_data["order_type"] = "Məkanda"
        await query.message.reply_text("🪑 Lütfən **Masa nömrənizi** daxil edin (məs: Masa 5):", parse_mode="Markdown")
        return GET_TABLE

    elif query.data == "type_delivery":
        context.user_data["order_type"] = "Çatdırılma"
        await query.message.reply_text("🛵 Lütfən **Çatdırılma ünvanını** daxil edin:", parse_mode="Markdown")
        return GET_ADDRESS


async def process_table_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["address"] = update.message.text
    context.user_data["phone"] = "-"
    return await complete_order(update, context)


async def process_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["address"] = update.message.text
    await update.message.reply_text("📞 Lütfən **Əlaqə nömrənizi** daxil edin:")
    return GET_PHONE


async def process_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    return await complete_order(update, context)


# YEKUN SİFARİŞİN YAZILMASI VƏ BİLDİRİŞLƏR
async def complete_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    restaurant_id = context.user_data.get("restaurant_id")
    cart = context.user_data.get("cart", {})
    user_id = update.effective_user.id

    order_type = context.user_data.get("order_type", "Məkanda")
    address = context.user_data.get("address", "-")
    phone = context.user_data.get("phone", "-")

    items_summary_list = []
    total_price = 0.0

    for item_id, item in cart.items():
        subtotal = item["price"] * item["count"]
        total_price += subtotal
        items_summary_list.append(f"{item['count']}x {item['name']} ({subtotal:.2f} AZN)")

    items_summary = ", ".join(items_summary_list)

    try:
        # 1. Airtable-a save olunur
        order_data = save_order(
            restaurant_id=restaurant_id,
            items_summary=items_summary,
            total_price=total_price,
            user_id=user_id,
            order_type=order_type,
            address=address,
            phone=phone,
        )

        # 2. Müştəriyə təsdiq mesajı
        info_text = f"📍 Masa: {address}" if order_type == "Məkanda" else f"📍 Ünvan: {address}\n📞 Tel: {phone}"
        
        await update.message.reply_text(
            f"🎉 **Sifarişiniz qəbul olundu!**\n\n"
            f"📌 **Növ:** {order_type}\n"
            f"{info_text}\n"
            f"💵 **Məbləğ:** {total_price:.2f} AZN\n\n"
            f"Yeməyiniz tezliklə hazırlanacaq! 👨‍🍳",
            parse_mode="Markdown",
        )

        # 3. Restoran Admininə Instant Bildiriş
        admin_chat_id = get_restaurant_admin_chat_id(restaurant_id)
        if admin_chat_id:
            try:
                admin_message = (
                    f"🔔 **YENİ SİFARİŞ QƏBUL OLUNDU!**\n\n"
                    f"🆔 **Sifariş ID:** `{order_data['order_id']}`\n"
                    f"📌 **Növ:** {order_type}\n"
                    f"{info_text}\n"
                    f"🛒 **Məhsullar:**\n{items_summary}\n\n"
                    f"💰 **Ümumi Məbləğ:** {total_price:.2f} AZN\n"
                    f"👤 **Müştəri ID:** `{user_id}`"
                )
                await context.bot.send_message(
                    chat_id=int(admin_chat_id),
                    text=admin_message,
                    parse_mode="Markdown",
                )
                logging.info(f"✅ Admin bildirişi göndərildi: {admin_chat_id}")
            except Exception as admin_err:
                logging.error(f"❌ Admin bildiriş xətası: {admin_err}")

        # Səbəti və state-ləri təmizləyirik
        context.user_data["cart"] = {}
        return ConversationHandler.END

    except Exception as err:
        logging.error("Sifariş xətası: %s", err)
        await update.message.reply_text("❌ Sifariş göndərilərkən xəta baş verdi. Yenidən cəhd edin.")
        return ConversationHandler.END


async def cancel_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ləğv edildi.")
    return ConversationHandler.END


def main():
    threading.Thread(target=run_web, daemon=True).start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Checkout üçün ConversationHandler
    checkout_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_checkout, pattern="^start_checkout$")],
        states={
            CHOOSING_TYPE: [CallbackQueryHandler(handle_order_type_choice, pattern="^type_")],
            GET_TABLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_table_input)],
            GET_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_address_input)],
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_phone_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_checkout)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CallbackQueryHandler(show_categories, pattern="^(show_menu|show_categories)$")
    )
    application.add_handler(CallbackQueryHandler(show_category_items, pattern="^cat_"))
    application.add_handler(CallbackQueryHandler(add_to_cart, pattern="^add_"))
    application.add_handler(CallbackQueryHandler(show_cart, pattern="^show_cart$"))
    application.add_handler(CallbackQueryHandler(clear_cart, pattern="^clear_cart$"))
    application.add_handler(
        CallbackQueryHandler(handle_cart_item_action, pattern="^(sub_|del_)")
    )
    application.add_handler(checkout_conv)

    print("🚀 Bot və Web server uğurla işə düşdü...")
    application.run_polling()


if __name__ == "__main__":
    main()
