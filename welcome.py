import html, time
import re
from typing import Optional, List

from telegram import Message, Chat, Update, Bot, User, CallbackQuery
from telegram import ParseMode, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
from telegram.ext import MessageHandler, Filters, CommandHandler, run_async, CallbackQueryHandler
from telegram.utils.helpers import mention_markdown, mention_html, escape_markdown

import tg_bot.modules.sql.welcome_sql as sql
from tg_bot import dispatcher, OWNER_ID, LOGGER, MESSAGE_DUMP
from tg_bot.modules.helper_funcs.chat_status import user_admin, is_user_ban_protected ,can_delete
from tg_bot.modules.helper_funcs.misc import build_keyboard, revert_buttons
from tg_bot.modules.helper_funcs.msg_types import get_welcome_type
from tg_bot.modules.helper_funcs.string_handling import markdown_parser, \
    escape_invalid_curly_brackets
from tg_bot.modules.log_channel import loggable

VALID_WELCOME_FORMATTERS = ['first', 'last', 'fullname', 'username', 'id', 'count', 'chatname', 'mention']

ENUM_FUNC_MAP = {
    sql.Types.TEXT.value: dispatcher.bot.send_message,
    sql.Types.BUTTON_TEXT.value: dispatcher.bot.send_message,
    sql.Types.STICKER.value: dispatcher.bot.send_sticker,
    sql.Types.DOCUMENT.value: dispatcher.bot.send_document,
    sql.Types.PHOTO.value: dispatcher.bot.send_photo,
    sql.Types.AUDIO.value: dispatcher.bot.send_audio,
    sql.Types.VOICE.value: dispatcher.bot.send_voice,
    sql.Types.VIDEO.value: dispatcher.bot.send_video
}


# do not async
def send(update, message, keyboard, backup_message):
    try:
        msg = update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    except IndexError:
        msg = update.effective_message.reply_text(markdown_parser(backup_message +
                                                                  "\nHinweis: Die aktuelle Willkommensnachricht "
                                                                  "ist aus Textbearbeitungsgründen nicht möglich. Das kann an dem "
                                                                  "Nutzername liegen."),
                                                  parse_mode=ParseMode.MARKDOWN)
    except KeyError:
        msg = update.effective_message.reply_text(markdown_parser(backup_message +
                                                                  "\nHinweis: Die aktuelle Willkommensnachricht "
                                                                  "Ist aufgrund von Platzhaltern nicht "
                                                                  "möglich. Bitte den Text prüfen und verbessern"),
                                                  parse_mode=ParseMode.MARKDOWN)
    except BadRequest as excp:
        if excp.message == "Button_url_invalid":
            msg = update.effective_message.reply_text(markdown_parser(backup_message +
                                                                      "\nHinweis: Die aktuelle Nachricht beinhaltet"
                                                                      "eine falsche URL in einem der Knöpfe. Bitte prüfen und verbessern."),
                                                      parse_mode=ParseMode.MARKDOWN)
        elif excp.message == "Unsupported url protocol":
            msg = update.effective_message.reply_text(markdown_parser(backup_message +
                                                                      "\nHinweis: Die aktuelle Nachricht beinhaltet Knöpfe, "
                                                                      "die von Telegram nicht unterstützte URL- Protokolle "
                                                                      "nutzen. Bitte überprüfen und updaten."),
                                                      parse_mode=ParseMode.MARKDOWN)
        elif excp.message == "Wrong url host":
            msg = update.effective_message.reply_text(markdown_parser(backup_message +
                                                                      "\nHinweis: Die aktuelle Nachricht beinhaltet falsche Links. "
                                                                      "Bitte überprüfen und verbessern."),
                                                      parse_mode=ParseMode.MARKDOWN)
            LOGGER.warning(message)
            LOGGER.warning(keyboard)
            LOGGER.exception("Parsen nicht möglich! Ungültiger URL Port gefunden!")
        else:
            msg = update.effective_message.reply_text(markdown_parser(backup_message +
                                                                      "\nHinweis: Bei dem Versuch, die neue Willkommenensnachricht zu senden, "
                                                                      "trat ein Fehler auf. Bitte den Text auf Fehler überprüfen und verbessern."),
                                                      parse_mode=ParseMode.MARKDOWN)
            LOGGER.exception()

    return msg


@run_async
def new_member(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]

    should_welc, cust_welcome, welc_type = sql.get_welc_pref(chat.id)
    if should_welc:
        sent = None
        new_members = update.effective_message.new_chat_members
        for new_mem in new_members:
            # Give the owner a special welcome
            if new_mem.id == OWNER_ID:973682688
                update.effective_message.reply_text("Yeah Chef, lass' uns diese Party rocken!")
                continue

            # Give start information when add bot to group
            elif new_mem.id == bot.id:
                continue
                update.effective_message.reply_text("Danka, dass du mich hinzugefügt hast!")

            else:
                # If welcome message is media, send with appropriate function
                if welc_type != sql.Types.TEXT and welc_type != sql.Types.BUTTON_TEXT:
                    ENUM_FUNC_MAP[welc_type](chat.id, cust_welcome)
                    return
                # else, move on
                first_name = new_mem.first_name or "PersonWithNoName"  # edge case of empty name - occurs for some bugs.

                if cust_welcome:
                    if new_mem.last_name:
                        fullname = "{} {}".format(first_name, new_mem.last_name)
                    else:
                        fullname = first_name
                    count = chat.get_members_count()
                    mention = mention_markdown(new_mem.id, first_name)
                    if new_mem.username:
                        username = "@" + escape_markdown(new_mem.username)
                    else:
                        username = mention

                    valid_format = escape_invalid_curly_brackets(cust_welcome, VALID_WELCOME_FORMATTERS)
                    res = valid_format.format(first=escape_markdown(first_name),
                                              last=escape_markdown(new_mem.last_name or first_name),
                                              fullname=escape_markdown(fullname), username=username, mention=mention,
                                              count=count, chatname=escape_markdown(chat.title), id=new_mem.id)
                    buttons = sql.get_welc_buttons(chat.id)
                    keyb = build_keyboard(buttons)
                else:
                    res = sql.DEFAULT_WELCOME.format(first=first_name)
                    keyb = []

                keyboard = InlineKeyboardMarkup(keyb)

                sent = send(update, res, keyboard,
                            sql.DEFAULT_WELCOME.format(first=first_name))  # type: Optional[Message]

                #Clean service welcome
                if sql.clean_service(chat.id) == True:
                    bot.delete_message(chat.id, update.message.message_id)

                #If user ban protected don't apply security on him
                if is_user_ban_protected(chat, new_mem.id, chat.get_member(new_mem.id)):
                    continue

                #Security soft mode
                if sql.welcome_security(chat.id) == "soft":
                    bot.restrict_chat_member(chat.id, new_mem.id, can_send_messages=True, can_send_media_messages=False, can_send_other_messages=False, can_add_web_page_previews=False, until_date=(int(time.time() + 24 * 60 * 60)))

                #Add "I'm not bot button if enabled hard security"
                if sql.welcome_security(chat.id) == "hard":
                    try:
                        #Mute user
                        bot.restrict_chat_member(chat.id, new_mem.id, can_send_messages=False, can_send_media_messages=False, can_send_other_messages=False, can_add_web_page_previews=False)
                        update.effective_message.reply_text("Hi {}, Klicke auf den Knopf unten, um zu bestätigen, dass du kein Bot bist!.".format(new_mem.first_name), 
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="Ich bin kein Bot!", 
                            callback_data="check_bot_({})".format(new_mem.id)) ]]))
                    except BadRequest:
                        update.effective_message.reply_text("Ich benötige die Berechtigung, Nutzer stummzuschalten, um Welcomesecurity zu aktivieren! :/")

        prev_welc = sql.get_clean_pref(chat.id)
        if prev_welc:
            try:
                bot.delete_message(chat.id, prev_welc)
            except BadRequest as excp:
                pass

            if sent:
                sql.set_clean_welcome(chat.id, sent.message_id)


@run_async
def check_bot_button(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    query = update.callback_query  # type: Optional[CallbackQuery]
    #bot.restrict_chat_member(chat.id, new_mem.id, can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)))
    match = re.match(r"check_bot_\((.+?)\)", query.data)
    user_id = int(match.group(1))
    message = update.effective_message  # type: Optional[Message]
    print(message)
    print(match, user.id, user_id)
    if user_id == user.id:
        print("JA!")
        query.answer(text="Entstummt!")
        #Unmute user
        bot.restrict_chat_member(chat.id, user.id, can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
        bot.deleteMessage(chat.id, message.message_id)
    else:
        print("NEIN")
        query.answer(text="Du bist kein neuer Nutzer!")
    #TODO need kick users after 2 hours and remove message 

@run_async
def left_member(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]
    should_goodbye, cust_goodbye, goodbye_type = sql.get_gdbye_pref(chat.id)
    if should_goodbye:
        left_mem = update.effective_message.left_chat_member
        if left_mem:
            # Ignore bot being kicked
            if left_mem.id == bot.id:
                return

            # Give the owner a special goodbye
            if left_mem.id == OWNER_ID:
                update.effective_message.reply_text("RIP, Chef")
                return

            # if media goodbye, use appropriate function for it
            if goodbye_type != sql.Types.TEXT and goodbye_type != sql.Types.BUTTON_TEXT:
                ENUM_FUNC_MAP[goodbye_type](chat.id, cust_goodbye)
                return

            first_name = left_mem.first_name or "PersonWithNoName"  # edge case of empty name - occurs for some bugs.
            if cust_goodbye:
                if left_mem.last_name:
                    fullname = "{} {}".format(first_name, left_mem.last_name)
                else:
                    fullname = first_name
                count = chat.get_members_count()
                mention = mention_markdown(left_mem.id, first_name)
                if left_mem.username:
                    username = "@" + escape_markdown(left_mem.username)
                else:
                    username = mention

                valid_format = escape_invalid_curly_brackets(cust_goodbye, VALID_WELCOME_FORMATTERS)
                res = valid_format.format(first=escape_markdown(first_name),
                                          last=escape_markdown(left_mem.last_name or first_name),
                                          fullname=escape_markdown(fullname), username=username, mention=mention,
                                          count=count, chatname=escape_markdown(chat.title), id=left_mem.id)
                buttons = sql.get_gdbye_buttons(chat.id)
                keyb = build_keyboard(buttons)

            else:
                res = sql.DEFAULT_GOODBYE
                keyb = []

            keyboard = InlineKeyboardMarkup(keyb)

            send(update, res, keyboard, sql.DEFAULT_GOODBYE)


@run_async
@user_admin
def welcome(bot: Bot, update: Update, args: List[str]):
    chat = update.effective_chat  # type: Optional[Chat]
    # if no args, show current replies.
    if len(args) == 0 or args[0].lower() == "noformat":
        noformat = args and args[0].lower() == "noformat"
        pref, welcome_m, welcome_type = sql.get_welc_pref(chat.id)
        update.effective_message.reply_text(
            "Der Chat hat seine Willkommenseinstellung gesetzt zu: `{}`.\n*Die Willkommensnachricht "
            "(ohne die ausgefüllten {{}}) lautet:*".format(pref),
            parse_mode=ParseMode.MARKDOWN)

        if welcome_type == sql.Types.BUTTON_TEXT:
            buttons = sql.get_welc_buttons(chat.id)
            if noformat:
                welcome_m += revert_buttons(buttons)
                update.effective_message.reply_text(welcome_m)

            else:
                keyb = build_keyboard(buttons)
                keyboard = InlineKeyboardMarkup(keyb)

                send(update, welcome_m, keyboard, sql.DEFAULT_WELCOME)

        else:
            if noformat:
                ENUM_FUNC_MAP[welcome_type](chat.id, welcome_m)

            else:
                ENUM_FUNC_MAP[welcome_type](chat.id, welcome_m, parse_mode=ParseMode.MARKDOWN)

    elif len(args) >= 1:
        if args[0].lower() in ("on", "yes"):
            sql.set_welc_preference(str(chat.id), True)
            update.effective_message.reply_text("Ich werde neue Nutzer begrüßen!")

        elif args[0].lower() in ("off", "no"):
            sql.set_welc_preference(str(chat.id), False)
            update.effective_message.reply_text("Ich schweige ab jetzt, wenn neue Nutzer der Gruppe beitreten.")

        else:
            # idek what you're writing, say yes or no
            update.effective_message.reply_text("Ich verstehe nur 'on/yes' oder 'off/no' !")


@run_async
@user_admin
def goodbye(bot: Bot, update: Update, args: List[str]):
    chat = update.effective_chat  # type: Optional[Chat]

    if len(args) == 0 or args[0] == "noformat":
        noformat = args and args[0] == "noformat"
        pref, goodbye_m, goodbye_type = sql.get_gdbye_pref(chat.id)
        update.effective_message.reply_text(
            "This chat has it's goodbye setting set to: `{}`.\n*The goodbye  message "
            "(not filling the {{}}) is:*".format(pref),
            parse_mode=ParseMode.MARKDOWN)

        if goodbye_type == sql.Types.BUTTON_TEXT:
            buttons = sql.get_gdbye_buttons(chat.id)
            if noformat:
                goodbye_m += revert_buttons(buttons)
                update.effective_message.reply_text(goodbye_m)

            else:
                keyb = build_keyboard(buttons)
                keyboard = InlineKeyboardMarkup(keyb)

                send(update, goodbye_m, keyboard, sql.DEFAULT_GOODBYE)

        else:
            if noformat:
                ENUM_FUNC_MAP[goodbye_type](chat.id, goodbye_m)

            else:
                ENUM_FUNC_MAP[goodbye_type](chat.id, goodbye_m, parse_mode=ParseMode.MARKDOWN)

    elif len(args) >= 1:
        if args[0].lower() in ("on", "yes"):
            sql.set_gdbye_preference(str(chat.id), True)
            update.effective_message.reply_text("Ich entschuldige mich, wenn Leute die Gruppe verlassen!")

        elif args[0].lower() in ("off", "no"):
            sql.set_gdbye_preference(str(chat.id), False)
            update.effective_message.reply_text("Wenn Leute die Gruppe verlassen, sind sie für mich gestorben.")

        else:
            # idek what you're writing, say yes or no
            update.effective_message.reply_text("Ich verstehe nur 'on/yes' oder 'off/no' !")


@run_async
@user_admin
@loggable
def set_welcome(bot: Bot, update: Update) -> str:
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    msg = update.effective_message  # type: Optional[Message]

    text, data_type, content, buttons = get_welcome_type(msg)

    if data_type is None:
        msg.reply_text("Du hast nicht angegeben, mit was ich antworten soll!")
        return ""

    sql.set_custom_welcome(chat.id, content or text, data_type, buttons)
    msg.reply_text("Die Willkommensnachricht wurde erfolgreich gesetzt!")

    return "<b>{}:</b>" \
           "\n#SET_WELCOME" \
           "\n<b>Admin:</b> {}" \
           "\nSetze die Willkommensnachricht".format(html.escape(chat.title),
                                               mention_html(user.id, user.first_name))


@run_async
@user_admin
@loggable
def reset_welcome(bot: Bot, update: Update) -> str:
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    sql.set_custom_welcome(chat.id, sql.DEFAULT_WELCOME, sql.Types.TEXT)
    update.effective_message.reply_text("Die Willkommensnachricht wurde erfolgreich auf Standard zurückgesetzt!")
    return "<b>{}:</b>" \
           "\n#RESET_WELCOME" \
           "\n<b>Admin:</b> {}" \
           "\nWillkommensnachricht auf Standard zurücksetzen.".format(html.escape(chat.title),
                                                            mention_html(user.id, user.first_name))


@run_async
@user_admin
@loggable
def set_goodbye(bot: Bot, update: Update) -> str:
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    msg = update.effective_message  # type: Optional[Message]
    text, data_type, content, buttons = get_welcome_type(msg)

    if data_type is None:
        msg.reply_text("Du hast nicht angegeben, mit was ich antworten soll!")
        return ""

    sql.set_custom_gdbye(chat.id, content or text, data_type, buttons)
    msg.reply_text("Ich habe die Verabschiedungsnachricht gesetzt!")
    return "<b>{}:</b>" \
           "\n#SET_GOODBYE" \
           "\n<b>Admin:</b> {}" \
           "\nVerabschiedungsnachricht setzen".format(html.escape(chat.title),
                                               mention_html(user.id, user.first_name))


@run_async
@user_admin
@loggable
def reset_goodbye(bot: Bot, update: Update) -> str:
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    sql.set_custom_gdbye(chat.id, sql.DEFAULT_GOODBYE, sql.Types.TEXT)
    update.effective_message.reply_text("Ciao- Nachricht auf Standard zurückgesetzt! ")
    return "<b>{}:</b>" \
           "\n#RESET_GOODBYE" \
           "\n<b>Admin:</b> {}" \
           "\nDie Abschieds- Nachricht zurücksetzen.".format(html.escape(chat.title),
                                                 mention_html(user.id, user.first_name))


@run_async
@user_admin
@loggable
def clean_welcome(bot: Bot, update: Update, args: List[str]) -> str:
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]

    if not args:
        clean_pref = sql.get_clean_pref(chat.id)
        if clean_pref:
            update.effective_message.reply_text("Ich sollte nun bis zu 2 Tage alte Willkommensnachrichten löschen.")
        else:
            update.effective_message.reply_text("Aktuell lasse ich alte Willkommensnachrichten stehen!")
        return ""

    if args[0].lower() in ("on", "yes"):
        sql.set_clean_welcome(str(chat.id), True)
        update.effective_message.reply_text("Yo, ich werde alte Begrüßungsnachrichten löschen!")
        return "<b>{}:</b>" \
               "\n#CLEAN_WELCOME" \
               "\n<b>Admin:</b> {}" \
               "\nAlte Begrüßungen löschen <code>AN</code>.".format(html.escape(chat.title),
                                                                         mention_html(user.id, user.first_name))
    elif args[0].lower() in ("off", "no"):
        sql.set_clean_welcome(str(chat.id), False)
        update.effective_message.reply_text("Ich lasse nun alte Willkommensnachrichten stehen.")
        return "<b>{}:</b>" \
               "\n#CLEAN_WELCOME" \
               "\n<b>Admin:</b> {}" \
               "\nIch werde alte Begrüßungen löschen: <code>NEIN</code>.".format(html.escape(chat.title),
                                                                          mention_html(user.id, user.first_name))
    else:
        # idek what you're writing, say yes or no
        update.effective_message.reply_text("Ich verstehe nur 'on/yes' oder 'off/no' !")
        return ""


@run_async
@user_admin
def security(bot: Bot, update: Update, args: List[str]) -> str:
    chat = update.effective_chat  # type: Optional[Chat]
    if len(args) >= 1:
        var = args[0]
        print(var)
        if (var == "no" or var == "off"):
            sql.set_welcome_security(chat.id, False)
            update.effective_message.reply_text("Bot-Schutz deaktiviert")
        elif(var == "soft"):
            sql.set_welcome_security(chat.id, "soft")
            update.effective_message.reply_text("Ich werde neuen Nutzern nach dem Beitreten 24h lang das Recht entziehen, Medien zu senden")
        elif(var == "hard"):
            sql.set_welcome_security(chat.id, "hard")
            update.effective_message.reply_text("New users will be muted if they do not click on the button")
        else:
            update.effective_message.reply_text("Bitte gib `off`/`no`/`soft`/`hard`ein!", parse_mode=ParseMode.MARKDOWN)
    else:
        status = sql.welcome_security(chat.id)
        update.effective_message.reply_text(status)


@run_async
@user_admin
def cleanservice(bot: Bot, update: Update, args: List[str]) -> str:
    chat = update.effective_chat  # type: Optional[Chat]
    if chat.type != chat.PRIVATE:
        if len(args) >= 1:
            var = args[0]
            print(var)
            if (var == "no" or var == "off"):
                sql.set_clean_service(chat.id, False)
                update.effective_message.reply_text("Ich lasse Servicenachrichten stehen.")
            elif(var == "yes" or var == "on"):
                sql.set_clean_service(chat.id, True)
                update.effective_message.reply_text("Ich werde Servicenachrichten löschen")
            else:
                update.effective_message.reply_text("Bitte gib 'yes' oder 'no' ein!", parse_mode=ParseMode.MARKDOWN)
        else:
            update.effective_message.reply_text("Bitte gib 'yes' oder 'no' ein!", parse_mode=ParseMode.MARKDOWN)
    else:
        update.effective_message.reply_text("Bitte gib 'yes' oder 'no' in deiner Gruppe ein!", parse_mode=ParseMode.MARKDOWN)


# TODO: get welcome data from group butler snap
# def __import_data__(chat_id, data):
#     welcome = data.get('info', {}).get('rules')
#     welcome = welcome.replace('$username', '{username}')
#     welcome = welcome.replace('$name', '{fullname}')
#     welcome = welcome.replace('$id', '{id}')
#     welcome = welcome.replace('$title', '{chatname}')
#     welcome = welcome.replace('$surname', '{lastname}')
#     welcome = welcome.replace('$rules', '{rules}')
#     sql.set_custom_welcome(chat_id, welcome, sql.Types.TEXT)


def __migrate__(old_chat_id, new_chat_id):
    sql.migrate_chat(old_chat_id, new_chat_id)


def __chat_settings__(bot, update, chat, chatP, user):
    chat_id = chat.id
    welcome_pref, _, _ = sql.get_welc_pref(chat_id)
    goodbye_pref, _, _ = sql.get_gdbye_pref(chat_id)
    return "Dieser Chat hat folgende Konfiguration für Willkommensnachrichten vorgenommen: `{}`.\n" \
           "Der Wert für Verabschiedungen ist `{}`.".format(welcome_pref, goodbye_pref)


__Hilfe__ = """
Die Willkommens/Abschiedsnachrichten deiner Gruppe können auf mehrere Weise personalisiert werden. Falls du die Willkommens/Abschiedsnachricht \
individuell setzen möchtest, kannst du gerne die unteren Variablen in deinen Willkommenstext schreiben, um sie anzuwenden. *Schau mal unten*:
 - `{{first}}`: Das zeigt den *Vornamen* des Nutzers in der Willkommensnachricht an 
 - `{{last}}`: Das zeigt den *Nachnamen* des neuen Nutzers an. Falls der Nutzer keinen Nachnamen besitzt, wird automatisch der Vorname in die Willkommensnachricht eingebaut
 - `{{fullname}}`: Baut den *vollen Namen* in die Willkommensnachricht ein
 - `{{username}}`: Markiert den neuen Nutzer in der Willkommensnachricht mit seinem @ nutzername. Es wird ein *Dauerlink* zu dem Nutzer eingebaut, falls dieser keinen Nutzername besitzt.
 - `{{mention}}`: Das *markiert* einen Nutzer einfach- Der Vorname des Nutzers wird dabei angezeigt.
 - `{{id}}`: Hierbei wird die *ID des Nutzers* in die Willkommensnachricht eingebaut.
 - `{{count}}`: Das zeigt die *Nummer aller Mitglieder des jeweiligen Chats* direkt bei dem Beitreten eines neuen Nutzers an.
 - `{{chatname}}`: Damit wird der Name des jeweiligen Chats eingebaut
Jede Variable *muss* mit `{{}}` eingeschlossen sein, ansonsten geht es net.
Willkommensnachrichten unterstützen auch *Fette* Schrift, Kursive oder auch unterstrichene Schriftzeichen. \
Knöpfe kann man ebenfalls einrichten, um den Text genial aussehen zu lassen. \
Um zum Beispiel einen Knopf hinzuzufügen, der zu Google führt, muss der Text so aussehen: `[Google](buttonurl://google.de/)`. \
Falls es dir Spaß macht, Kannst du auch Fotos/GIFs/Videos und Sprachnachrichten als Willkommensnachricht setzen, indem du \
dem Medium antwortest, und als Antwortsatz zu dieser Nachricht dann /setwelcome sendest.

*Nur für Gruppenadmins:*
 - /welcome <on/off>: Aktivieren/ Deaktivieren der Willkommensnachrichten.
 - /welcome: Zeigt die aktuellen Willkommens- Einsgellungen an.
 - /welcome noformat: Zeigt deine aktuelle Willkommensnachricht an - nützlich, um deine Willkommensnachricht wiederzuverwenden!
 - /goodbye -> gleiche Nutzung und Anzeige wie bei /welcome.
 - /setwelcome <irgendwas>: set a custom welcome . If used replying to media, uses that media.
 - /setgoodbye <irgendwas>: Eine Perosonalisierte Nachricht zum Begrüßen von neuen Mitgliedern setzen.
 - /resetwelcome: Zu der Standard Nachricht zum Begrüßen von neuen Mitgliedern zurückkehren.
 - /resetgoodbye: Zu der Standard Nachricht zum Verabschieden von Mitgliedern zurückkehren.
 - /cleanwelcome <on/off>: Die alten Begrüßungsnachrichten automatisch löschen lassen, um die Gruppe sauber zu halten
 - /cleanservice <on/off/yes/no>: Löscht alle Servicenachrichten; das sind die "x ist der Gruppe beigetreten" Meldungen, die ziemlich nervig sein können.
 - /welcomesecurity <off/soft/hard>: soft - neuen Nutzern das Recht , Medien in die Gruppe zu senden, für die ersten 24h abnehmen; hard - Nimmt dem Nutzer das Recht, Nachrichten zu senden, bis er den Knopf "Ich bin kein Roboter" klickt
""".format(dispatcher.bot.username)


__mod_name__ = "Welcomes/Goodbyes"

NEW_MEM_HANDLER = MessageHandler(Filters.status_update.new_chat_members, new_member)
LEFT_MEM_HANDLER = MessageHandler(Filters.status_update.left_chat_member, left_member)
WELC_PREF_HANDLER = CommandHandler("welcome", welcome, pass_args=True, filters=Filters.group)
GOODBYE_PREF_HANDLER = CommandHandler("goodbye", goodbye, pass_args=True, filters=Filters.group)
SET_WELCOME = CommandHandler("setwelcome", set_welcome, filters=Filters.group)
SET_GOODBYE = CommandHandler("setgoodbye", set_goodbye, filters=Filters.group)
RESET_WELCOME = CommandHandler("resetwelcome", reset_welcome, filters=Filters.group)
RESET_GOODBYE = CommandHandler("resetgoodbye", reset_goodbye, filters=Filters.group)
CLEAN_WELCOME = CommandHandler("cleanwelcome", clean_welcome, pass_args=True, filters=Filters.group)

SECURITY_HANDLER = CommandHandler("welcomesecurity", security, pass_args=True, filters=Filters.group)
CLEAN_SERVICE_HANDLER = CommandHandler("cleanservice", cleanservice, pass_args=True, filters=Filters.group)

help_callback_handler = CallbackQueryHandler(check_bot_button, pattern=r"check_bot_")

dispatcher.add_handler(NEW_MEM_HANDLER)
dispatcher.add_handler(LEFT_MEM_HANDLER)
dispatcher.add_handler(WELC_PREF_HANDLER)
dispatcher.add_handler(GOODBYE_PREF_HANDLER)
dispatcher.add_handler(SET_WELCOME)
dispatcher.add_handler(SET_GOODBYE)
dispatcher.add_handler(RESET_WELCOME)
dispatcher.add_handler(RESET_GOODBYE)
dispatcher.add_handler(CLEAN_WELCOME)
dispatcher.add_handler(SECURITY_HANDLER)
dispatcher.add_handler(CLEAN_SERVICE_HANDLER)

dispatcher.add_handler(help_callback_handler)

