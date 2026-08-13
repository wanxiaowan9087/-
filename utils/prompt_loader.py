from utils.config_handler import prompts_config
from utils.path_tool import get_abs_path
from utils.logger_handler import logger


def load_system_prompts():
    try:
        system_prompts_path=get_abs_path(prompts_config["main_prompts_path"])
    except KeyError as e:
        logger.error(f"[load_system_prompts]在yaml配置当中不存在main_prompts_path")
        raise e

    try:
        return open(system_prompts_path,"r",encoding="utf-8").read()
    except Exception as e:
        logger.error(f"[load_system_prompts]解析系统提示词出错:{str(e)}")
        raise e


def load_rag_prompts():
    try:
        system_prompts_path=get_abs_path(prompts_config["rag_summarize_path"])
    except KeyError as e:
        logger.error(f"[load_rag_prompts]在yaml配置当中不存在rag_summarize_path")
        raise e

    try:
        return open(system_prompts_path,"r",encoding="utf-8").read()
    except Exception as e:
        logger.error(f"[load_rag_prompts]解析Rag总结提示词出错:{str(e)}")
        raise e


def load_report_prompts():
    try:
        system_prompts_path=get_abs_path(prompts_config["report_prompts_path"])
    except KeyError as e:
        logger.error(f"[load_report_prompts]在yaml配置当中不存在report_prompts_path")
        raise e

    try:
        return open(system_prompts_path,"r",encoding="utf-8").read()
    except Exception as e:
        logger.error(f"[load_report_prompts]解析报告生成提示词出错:{str(e)}")
        raise e

if __name__ == "__main__":
    print(load_rag_prompts())