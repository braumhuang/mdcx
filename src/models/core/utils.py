"""
刮削过程的一般工具函数
依赖:
    此模块不应依赖 models.core 中除 flags 外的任何其他模块
"""

import json
import os
import re
import shutil
import subprocess
import traceback
import unicodedata
from typing import Optional

from bridge.typing import LIST, SET, DICT, TUPLE
from ..base.file import read_link, split_path
from ..base.number import get_number_letters
from ..base.path import get_path
from ..base.utils import convert_path, get_used_time
from ..config.consts import IS_NFC
from ..config.manager import config, manager
from ..config.manual import ManualConfig
from ..config.resources import resources
from ..signals import signal
from .json_data import JsonData, LogBuffer

try:
    import cv2

    has_opencv = True
except ImportError:
    has_opencv = False


def replace_word(json_data: JsonData):
    # 常见字段替换的字符
    for key, value in ManualConfig.ALL_REP_WORD.items():
        for each in ManualConfig.ALL_KEY_WORD:
            json_data[each] = json_data[each].replace(key, value)

    # 简体时替换的字符
    key_word = []
    if config.title_language == "zh_cn":
        key_word.append("title")
    if config.outline_language == "zh_cn":
        key_word.append("outline")

    for key, value in ManualConfig.CHINESE_REP_WORD.items():
        for each in key_word:
            json_data[each] = json_data[each].replace(key, value)

    # 替换标题的上下集信息
    fields_word = ["title", "originaltitle"]
    for field in fields_word:
        for each in ManualConfig.TITLE_REP:
            json_data[field] = json_data[field].replace(each, "").strip(":， ").strip()


def show_movie_info(json_data: JsonData):
    if not config.show_data_log:  # 调试模式打开时显示详细日志
        return
    for key in ManualConfig.SHOW_KEY:
        value = json_data.get(key)
        if not value:
            continue
        if key == "outline" or key == "originalplot" and len(value) > 100:
            value = str(value)[:98] + "……（略）"
        elif key == "has_sub":
            value = "中文字幕"
        elif key == "actor" and "actor_all," in config.nfo_include_new:
            value = json_data["all_actor"]
        LogBuffer.log().write("\n     " + "%-13s" % key + ": " + str(value))


has_ffprobe = True if shutil.which("ffprobe") else False


def _get_video_metadata_opencv(file_path: str) -> TUPLE[int, str]:
    cap = cv2.VideoCapture(file_path)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ##使用opencv获取编码器格式
    codec = int(cap.get(cv2.CAP_PROP_FOURCC))
    codec_fourcc = chr(codec & 0xFF) + chr((codec >> 8) & 0xFF) + chr((codec >> 16) & 0xFF) + chr((codec >> 24) & 0xFF)
    return height, codec_fourcc


def _get_video_metadata_ffmpeg(file_path: str) -> TUPLE[int, str]:
    if not has_ffprobe:
        raise RuntimeError("当前版本无 opencv. 若想获取视频分辨率请请安装 ffprobe 或改用带 opencv 版本.")
    # Use ffprobe to get video information
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path]

    # macOS and Linux use default flags
    creationflags = 0
    # Windows use CREATE_NO_WINDOW to suppress the console window
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW

    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)

    data = json.loads(result.stdout)

    # Find video stream
    video_stream = next((stream for stream in data["streams"] if stream["codec_type"] == "video"), None)

    if video_stream:
        height = int(video_stream["height"])
        codec_fourcc = video_stream["codec_name"].upper()
    else:
        height = 0
        codec_fourcc = ""
    return height, codec_fourcc


_get_video_metadata = _get_video_metadata_opencv if has_opencv else _get_video_metadata_ffmpeg


def get_video_size(json_data: JsonData, file_path: str):
    # 获取本地分辨率 同时获取视频编码格式
    definition = ""
    height = 0
    hd_get = config.hd_get
    if os.path.islink(file_path):
        if "symlink_definition" in config.no_escape:
            file_path = read_link(file_path)
        else:
            hd_get = "path"
    if hd_get == "video":
        try:
            height, codec_fourcc = _get_video_metadata(file_path)
        except Exception as e:
            signal.show_log_text(f" 🔴 无法获取视频分辨率! 文件地址: {file_path}  错误信息: {e}")
    elif hd_get == "path":
        file_path_temp = file_path.upper()
        if "8K" in file_path_temp:
            height = 4000
        elif "4K" in file_path_temp or "UHD" in file_path_temp:
            height = 2000
        elif "1440P" in file_path_temp or "QHD" in file_path_temp:
            height = 1440
        elif "1080P" in file_path_temp or "FHD" in file_path_temp:
            height = 1080
        elif "960P" in file_path_temp:
            height = 960
        elif "720P" in file_path_temp or "HD" in file_path_temp:
            height = 720

    hd_name = config.hd_name
    if not height:
        pass
    elif height >= 4000:
        definition = "8K" if hd_name == "height" else "UHD8"
    elif height >= 2000:
        definition = "4K" if hd_name == "height" else "UHD"
    elif height >= 1400:
        definition = "1440P" if hd_name == "height" else "QHD"
    elif height >= 1000:
        definition = "1080P" if hd_name == "height" else "FHD"
    elif height >= 900:
        definition = "960P" if hd_name == "height" else "HD"
    elif height >= 700:
        definition = "720P" if hd_name == "height" else "HD"
    elif height >= 500:
        definition = "540P" if hd_name == "height" else "qHD"
    elif height >= 400:
        definition = "480P"
    elif height >= 300:
        definition = "360P"
    elif height >= 100:
        definition = "144P"
    json_data["definition"] = definition

    if definition in ["4K", "8K", "UHD", "UHD8"]:
        json_data["_4K"] = "-" + definition

    # 去除标签中的分辨率率，使用本地读取的实际分辨率
    remove_key = ["144P", "360P", "480P", "540P", "720P", "960P", "1080P", "1440P", "2160P", "4K", "8K"]
    tag = json_data["tag"]
    for each_key in remove_key:
        tag = tag.replace(each_key, "").replace(each_key.lower(), "")
    tag_list = re.split(r"[,，]", tag)
    new_tag_list = []
    [new_tag_list.append(i) for i in tag_list if i]
    if definition and "definition" in config.tag_include:
        new_tag_list.insert(0, definition)
        if hd_get == "video":
            new_tag_list.insert(0, codec_fourcc.upper())  # 插入编码格式
    json_data["tag"] = "，".join(new_tag_list)
    return json_data


def show_data_result(json_data: JsonData, start_time: float):
    if json_data["title"] == "":
        LogBuffer.log().write(
            f"\n 🌐 [website] {LogBuffer.req().get().strip('-> ')}"
            f"\n{LogBuffer.info().get().strip()}"
            f"\n 🔴 Data failed!({get_used_time(start_time)}s)"
        )
        return False
    else:
        if config.show_web_log:  # 字段刮削过程
            LogBuffer.log().write(f"\n 🌐 [website] {LogBuffer.req().get().strip('-> ')}")
        try:
            LogBuffer.log().write("\n" + LogBuffer.info().get().strip(" ").strip("\n"))
        except Exception:
            signal.show_log_text(traceback.format_exc())
        if config.show_from_log:  # 字段来源信息
            if json_data["fields_info"]:
                LogBuffer.log().write("\n" + json_data["fields_info"].strip(" ").strip("\n"))
        LogBuffer.log().write(f"\n 🍀 Data done!({get_used_time(start_time)}s)")
        return True


def deal_url(url: str) -> TUPLE[Optional[str], str]:
    if "://" not in url:
        url = "https://" + url
    url = url.strip()
    for key, vlaue in ManualConfig.WEB_DIC.items():
        if key.lower() in url.lower():
            return vlaue, url

    # 自定义的网址
    for web_name in ManualConfig.SUPPORTED_WEBSITES:
        if hasattr(config, web_name + "_website"):
            web_url = getattr(config, web_name + "_website")
            if web_url in url:
                return web_name, url

    return None, url


def replace_special_word(json_data: JsonData):
    # 常见字段替换的字符
    all_key_word = [
        "title",
        "originaltitle",
        "outline",
        "originalplot",
        "series",
        "director",
        "studio",
        "publisher",
        "tag",
    ]
    for key, value in ManualConfig.SPECIAL_WORD.items():
        for each in all_key_word:
            json_data[each] = json_data[each].replace(key, value)


def convert_half(string: str) -> str:
    # 替换敏感词
    for key, value in ManualConfig.SPECIAL_WORD.items():
        string = string.replace(key, value)
    # 替换全角为半角
    for each in ManualConfig.FULL_HALF_CHAR:
        string = string.replace(each[0], each[1])
    # 去除空格等符号
    return re.sub(r"[\W_]", "", string).upper()


def get_new_release(release: str) -> str:
    release_rule = config.release_rule
    if not release:
        release = "0000-00-00"
    if release_rule == "YYYY-MM-DD":
        return release
    year, month, day = re.findall(r"(\d{4})-(\d{2})-(\d{2})", release)[0]
    return release_rule.replace("YYYY", year).replace("YY", year[-2:]).replace("MM", month).replace("DD", day)


def nfd2c(path: str) -> str:
    # 转换 NFC(mac nfc和nfd都能访问到文件，但是显示的是nfd，这里统一使用nfc，避免各种问题。
    # 日文浊音转换（mac的坑，osx10.12以下使用nfd，以上兼容nfc和nfd，只是显示成了nfd）
    if IS_NFC:
        new_path = unicodedata.normalize("NFC", path)  # Mac 会拆成两个字符，即 NFD，windwos是 NFC
    else:
        new_path = unicodedata.normalize("NFD", path)  # Mac 会拆成两个字符，即 NFD，windwos是 NFC
    return new_path


def deal_some_field(json_data: JsonData) -> JsonData:
    fields_rule = config.fields_rule
    actor = json_data["actor"]
    title = json_data["title"]
    originaltitle = json_data["originaltitle"]
    number = json_data["number"]

    # 演员处理
    if actor:
        # 去除演员名中的括号
        new_actor_list = []
        actor_list = []
        temp_actor_list = []
        for each_actor in actor.split(","):
            if each_actor and each_actor not in actor_list:
                actor_list.append(each_actor)
                new_actor = re.findall(r"[^\(\)\（\）]+", each_actor)
                if new_actor[0] not in new_actor_list:
                    new_actor_list.append(new_actor[0])
                temp_actor_list.extend(new_actor)
        if "del_char" in fields_rule:
            json_data["actor"] = ",".join(new_actor_list)
        else:
            json_data["actor"] = ",".join(actor_list)

        # 去除标题后的演员名
        if "del_actor" in fields_rule:
            new_all_actor_name_list = []
            for each_actor in json_data["actor_amazon"] + temp_actor_list:
                actor_keyword_list = resources.get_actor_data(each_actor).get(
                    "keyword"
                )  # 获取演员映射表的所有演员别名进行替换
                new_all_actor_name_list.extend(actor_keyword_list)
            for each_actor in set(new_all_actor_name_list):
                try:
                    end_actor = re.compile(rf" {each_actor}$")
                    title = re.sub(end_actor, "", title)
                    originaltitle = re.sub(end_actor, "", originaltitle)
                except Exception:
                    signal.show_traceback_log(traceback.format_exc())
        json_data["title"] = title.strip()
        json_data["originaltitle"] = originaltitle.strip()

    # 去除标题中的番号
    if number != title and title.startswith(number):
        title = title.replace(number, "").strip()
        json_data["title"] = title
    if number != originaltitle and originaltitle.startswith(number):
        originaltitle = originaltitle.replace(number, "").strip()
        json_data["originaltitle"] = originaltitle

    # 去除标题中的/
    json_data["title"] = json_data["title"].replace("/", "#").strip(" -")
    json_data["originaltitle"] = json_data["originaltitle"].replace("/", "#").strip(" -")

    # 去除素人番号前缀数字
    if "del_num" in fields_rule:
        temp_n = re.findall(r"\d{3,}([a-zA-Z]+-\d+)", number)
        if temp_n:
            json_data["number"] = temp_n[0]
            json_data["letters"] = get_number_letters(json_data["number"])

    if number.endswith("Z"):
        json_data["number"] = json_data["number"][:-1] + "z"
    return json_data


def get_movie_path_setting(file_path="") -> TUPLE[str, str, str, LIST[str], str, str]:
    # 先把'\'转成'/'以便判断是路径还是目录
    movie_path = config.media_path.replace("\\", "/")  # 用户设置的扫描媒体路径
    if movie_path == "":  # 未设置为空时，使用用户数据目录
        movie_path = manager.data_folder
    movie_path = nfd2c(movie_path)
    end_folder_name = split_path(movie_path)[1]
    # 用户设置的软链接输出目录
    softlink_path = config.softlink_path.replace("\\", "/").replace("end_folder_name", end_folder_name)
    # 用户设置的成功输出目录
    success_folder = config.success_output_folder.replace("\\", "/").replace("end_folder_name", end_folder_name)
    # 用户设置的失败输出目录
    failed_folder = config.failed_output_folder.replace("\\", "/").replace("end_folder_name", end_folder_name)
    # 用户设置的排除目录
    escape_folder_list = (
        config.folders.replace("\\", "/").replace("end_folder_name", end_folder_name).replace("，", ",").split(",")
    )
    # 用户设置的剧照副本目录
    extrafanart_folder = config.extrafanart_folder.replace("\\", "/")

    # 获取路径
    softlink_path = convert_path(get_path(movie_path, softlink_path))
    success_folder = convert_path(get_path(movie_path, success_folder))
    failed_folder = convert_path(get_path(movie_path, failed_folder))
    softlink_path = nfd2c(softlink_path)
    success_folder = nfd2c(success_folder)
    failed_folder = nfd2c(failed_folder)
    extrafanart_folder = nfd2c(extrafanart_folder)

    # 获取排除目录完整路径（尾巴添加/）
    escape_folder_new_list = []
    for es in escape_folder_list:  # 排除目录可以多个，以，,分割
        es = es.strip(" ")
        if es:
            es = get_path(movie_path, es).replace("\\", "/")
            if es[-1] != "/":  # 路径尾部添加“/”，方便后面move_list查找时匹配路径
                es += "/"
            es = nfd2c(es)
            escape_folder_new_list.append(es)

    if file_path:
        temp_path = movie_path
        if config.scrape_softlink_path:
            temp_path = softlink_path
        if "first_folder_name" in success_folder or "first_folder_name" in failed_folder:
            first_folder_name = re.findall(r"^/?([^/]+)/", file_path[len(temp_path) :].replace("\\", "/"))
            first_folder_name = first_folder_name[0] if first_folder_name else ""
            success_folder = success_folder.replace("first_folder_name", first_folder_name)
            failed_folder = failed_folder.replace("first_folder_name", first_folder_name)

    return (
        convert_path(movie_path),
        success_folder,
        failed_folder,
        escape_folder_new_list,
        extrafanart_folder,
        softlink_path,
    )
