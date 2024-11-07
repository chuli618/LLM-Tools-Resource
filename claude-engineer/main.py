import os
from dotenv import load_dotenv
import json
from tavily import TavilyClient
import base64
from PIL import Image
import io
import re
from anthropic import Anthropic, APIStatusError, APIError
import difflib
import time
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
import difflib
from prompt_toolkit.completion import WordCompleter
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
import datetime
import venv
import sys
import signal
import logging
from typing import Tuple, Optional

'''
1. main() 是最顶层函数,控制整体流程。
2. chat_with_claude() 是第二层核心函数,处理与AI的交互。
3. execute_tool() 是第三层函数,根据需要调用各种工具函数。
4. 其他函数如 edit_and_apply_multiple(), execute_code() 等是第四层函数,执行具体的任务。
5. 一些辅助函数如 display_token_usage(), generate_diff() 等被多个层次的函数调用。
'''

async def get_user_input(prompt="You: "):
    """
    异步获取用户输入的函数。
    
    参数:
    prompt (str): 提示用户输入的字符串，默认为"You: ".
    
    返回:
    str: 用户输入的内容。

    调用的外部函数:
    - PromptSession(): 创建一个提示会话对象，用于获取用户输入。
    - Style.from_dict(): 创建一个样式对象，用于设置提示的样式。
    """
    # 设置提示样式为青色加粗
    style = Style.from_dict({
        'prompt': 'cyan bold',
    })
    # 创建一个提示会话
    session = PromptSession(style=style)
    # 异步获取用户输入，禁止多行输入
    return await session.prompt_async(prompt, multiline=False)

async def get_format_choice():
    """
    异步获取用户选择的格式的函数。
    
    返回:
    str: 用户选择的格式，转换为小写（Markdown或JSON）。

    调用的外部函数:
    - WordCompleter(): 创建一个自动补全器，用于提供格式选项。
    - Style.from_dict(): 创建一个样式对象，用于设置补全菜单的样式。
    - PromptSession(): 创建一个提示会话对象，用于获取用户输入。
    """
    # 创建一个自动补全器，支持Markdown和JSON格式
    completer = WordCompleter(['Markdown', 'JSON'], ignore_case=True)
    # 设置补全菜单的样式
    style = Style.from_dict({
        'completion-menu.completion': 'bg:#008888 #ffffff',
        'completion-menu.completion.current': 'bg:#00aaaa #000000',
        'scrollbar.background': 'bg:#88aaaa',
        'scrollbar.button': 'bg:#222222',
    })

    # 创建一个提示会话，支持补全
    session = PromptSession(completer=completer, style=style, complete_while_typing=True)

    # 异步提示用户选择格式
    result = await session.prompt_async('Choose format [Markdown/JSON] (Tab to select): ', multiline=False)
    # 返回用户选择的格式，转换为小写
    return result.lower()

def setup_virtual_environment() -> Tuple[str, str]:
    """
    设置虚拟环境的函数。
    
    返回:
    Tuple[str, str]: 返回虚拟环境的路径和激活脚本的路径。

    调用的外部函数:
    - os.path.join(): 连接路径名组件。
    - os.getcwd(): 获取当前工作目录。
    - venv.create(): 创建虚拟环境。
    - logging.error(): 记录错误信息。
    """
    # 定义虚拟环境的名称
    venv_name = "code_execution_env"
    # 获取虚拟环境的路径
    venv_path = os.path.join(os.getcwd(), venv_name)
    try:
        # 如果虚拟环境不存在，则创建它
        if not os.path.exists(venv_path):
            venv.create(venv_path, with_pip=True)
        
        # 激活虚拟环境的脚本路径
        if sys.platform == "win32":
            activate_script = os.path.join(venv_path, "Scripts", "activate.bat")
        else:
            activate_script = os.path.join(venv_path, "bin", "activate")
        
        # 返回虚拟环境路径和激活脚本路径
        return venv_path, activate_script
    except Exception as e:
        # 记录错误信息
        logging.error(f"Error setting up virtual environment: {str(e)}")
        raise

# 从.env文件加载环境变量
load_dotenv()

# 初始化Anthropic客户端
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
if not anthropic_api_key:
    # 如果没有找到API密钥，则抛出错误
    raise ValueError("ANTHROPIC_API_KEY not found in environment variables")
anthropic_base_url = os.getenv("ANTHROPIC_BASE_URL")
if not anthropic_base_url:
    # 初始化Anthropic客户端
    client = Anthropic(api_key=anthropic_api_key, base_url=anthropic_base_url)
else:
    client = Anthropic(api_key=anthropic_api_key)

# 初始化Tavily客户端
tavily_api_key = os.getenv("TAVILY_API_KEY")
if not tavily_api_key:
    # 如果没有找到Tavily API密钥，则抛出错误
    raise ValueError("TAVILY_API_KEY not found in environment variables")
tavily = TavilyClient(api_key=tavily_api_key)

# 创建控制台对象
console = Console()

# token跟踪变量
main_model_tokens = {'input': 0, 'output': 0}
tool_checker_tokens = {'input': 0, 'output': 0}
code_editor_tokens = {'input': 0, 'output': 0}
code_execution_tokens = {'input': 0, 'output': 0}

# 使用模糊搜索的标志
USE_FUZZY_SEARCH = True

# 设置对话记忆（维护MAINMODEL的上下文）
conversation_history = []

# 存储文件内容（MAINMODEL的上下文的一部分）
file_contents = {}

# 代码编辑器记忆（在调用之间维护CODEEDITORMODEL的一些上下文）
code_editor_memory = []

# 代码编辑器上下文中已经存在的文件
code_editor_files = set()

# 自动模式标志
automode = False

# 存储文件内容
file_contents = {}

# 存储正在运行的进程的全局字典
running_processes = {}

# 常量
CONTINUATION_EXIT_PHRASE = "AUTOMODE_COMPLETE"
MAX_CONTINUATION_ITERATIONS = 25
MAX_CONTEXT_TOKENS = 200000  # 将上下文窗口的最大token数减少到200k

# 模型名称
MAINMODEL = "claude-3-5-sonnet-20240620"
TOOLCHECKERMODEL = "claude-3-5-sonnet-20240620"
CODEEDITORMODEL = "claude-3-5-sonnet-20240620"
CODEEXECUTIONMODEL = "claude-3-5-sonnet-20240620"

# 系统提示
BASE_SYSTEM_PROMPT = """
You are Claude, an AI assistant powered by Anthropic's Claude-3.5-Sonnet model, specialized in software development with access to a variety of tools and the ability to instruct and direct a coding agent and a code execution one. Your capabilities include:

1. Creating and managing project structures
2. Writing, debugging, and improving code across multiple languages
3. Providing architectural insights and applying design patterns
4. Staying current with the latest technologies and best practices
5. Analyzing and manipulating files within the project directory
6. Performing web searches for up-to-date information
7. Executing code and analyzing its output within an isolated 'code_execution_env' virtual environment
8. Managing and stopping running processes started within the 'code_execution_env'

Available tools and their optimal use cases:

1. create_folder: Create new directories in the project structure.
2. create_files: Generate multiple new files with specified content. Strive to make the files as complete and useful as possible.
3. edit_and_apply_multiple: Examine and modify multiple existing files by instructing a separate AI coding agent. You are responsible for providing clear, detailed instructions for each file. When using this tool:
   - Provide comprehensive context about the project, including recent changes, new variables or functions, and how files are interconnected.
   - Clearly state the specific changes or improvements needed for each file, explaining the reasoning behind each modification.
   - Include ALL the snippets of code to change, along with the desired modifications.
   - Specify coding standards, naming conventions, or architectural patterns to be followed.
   - Anticipate potential issues or conflicts that might arise from the changes and provide guidance on how to handle them.
   - Anticipate potential issues or conflicts that might arise from the changes and provide guidance on how to handle them.
4. execute_code: Run Python code exclusively in the 'code_execution_env' virtual environment and analyze its output. Use this when you need to test code functionality or diagnose issues. Remember that all code execution happens in this isolated environment. This tool now returns a process ID for long-running processes.
5. stop_process: Stop a running process by its ID. Use this when you need to terminate a long-running process started by the execute_code tool.
6. read_file: Read the contents of an existing file.
7. read_multiple_files: Read the contents of multiple existing files at once. Use this when you need to examine or work with multiple files simultaneously.
8. list_files: List all files and directories in a specified folder.
9. tavily_search: Perform a web search using the Tavily API for up-to-date information.

Tool Usage Guidelines:
- Always use the most appropriate tool for the task at hand.
- Provide detailed and clear instructions when using tools, especially for edit_and_apply.
- After making changes, always review the output to ensure accuracy and alignment with intentions.
- Use execute_code to run and test code within the 'code_execution_env' virtual environment, then analyze the results.
- For long-running processes, use the process ID returned by execute_code to stop them later if needed.
- Proactively use tavily_search when you need up-to-date information or additional context.
- When working with multiple files, consider using read_multiple_files for efficiency.

Error Handling and Recovery:
- If a tool operation fails, carefully analyze the error message and attempt to resolve the issue.
- For file-related errors, double-check file paths and permissions before retrying.
- If a search fails, try rephrasing the query or breaking it into smaller, more specific searches.
- If code execution fails, analyze the error output and suggest potential fixes, considering the isolated nature of the environment.
- If a process fails to stop, consider potential reasons and suggest alternative approaches.

Project Creation and Management:
1. Start by creating a root folder for new projects.
2. Create necessary subdirectories and files within the root folder.
3. Organize the project structure logically, following best practices for the specific project type.

Always strive for accuracy, clarity, and efficiency in your responses and actions. Your instructions must be precise and comprehensive. If uncertain, use the tavily_search tool or admit your limitations. When executing code, always remember that it runs in the isolated 'code_execution_env' virtual environment. Be aware of any long-running processes you start and manage them appropriately, including stopping them when they are no longer needed.

When using tools:
1. Carefully consider if a tool is necessary before using it.
2. Ensure all required parameters are provided and valid.
3. Handle both successful results and errors gracefully.
4. Provide clear explanations of tool usage and results to the user.

Remember, you are an AI assistant, and your primary goal is to help the user accomplish their tasks effectively and efficiently while maintaining the integrity and security of their development environment.
"""

AUTOMODE_SYSTEM_PROMPT = """
You are currently in automode. Follow these guidelines:

1. Goal Setting:
   - Set clear, achievable goals based on the user's request.
   - Break down complex tasks into smaller, manageable goals.

2. Goal Execution:
   - Work through goals systematically, using appropriate tools for each task.
   - Utilize file operations, code writing, and web searches as needed.
   - Always read a file before editing and review changes after editing.

3. Progress Tracking:
   - Provide regular updates on goal completion and overall progress.
   - Use the iteration information to pace your work effectively.

4. Tool Usage:
   - Leverage all available tools to accomplish your goals efficiently.
   - Prefer edit_and_apply for file modifications, applying changes in chunks for large edits.
   - Use tavily_search proactively for up-to-date information.

5. Error Handling:
   - If a tool operation fails, analyze the error and attempt to resolve the issue.
   - For persistent errors, consider alternative approaches to achieve the goal.

6. Automode Completion:
   - When all goals are completed, respond with "AUTOMODE_COMPLETE" to exit automode.
   - Do not ask for additional tasks or modifications once goals are achieved.

7. Iteration Awareness:
   - You have access to this {iteration_info}.
   - Use this information to prioritize tasks and manage time effectively.

Remember: Focus on completing the established goals efficiently and effectively. Avoid unnecessary conversations or requests for additional tasks.
"""

def update_system_prompt(current_iteration: Optional[int] = None, max_iterations: Optional[int] = None) -> str:
    """
    更新系统提示的函数。
    
    参数:
    current_iteration (Optional[int]): 当前迭代次数。
    max_iterations (Optional[int]): 最大迭代次数。
    
    返回:
    str: 更新后的系统提示字符串。

    调用的外部函数:
    - json.dumps(): 将Python对象转换为JSON字符串。
    """
    global file_contents
    chain_of_thought_prompt = """
    Answer the user's request using relevant tools (if they are available). Before calling a tool, do some analysis within <thinking></thinking> tags. First, think about which of the provided tools is the relevant tool to answer the user's request. Second, go through each of the required parameters of the relevant tool and determine if the user has directly provided or given enough information to infer a value. When deciding if the parameter can be inferred, carefully consider all the context to see if it supports a specific value. If all of the required parameters are present or can be reasonably inferred, close the thinking tag and proceed with the tool call. BUT, if one of the values for a required parameter is missing, DO NOT invoke the function (not even with fillers for the missing params) and instead, ask the user to provide the missing parameters. DO NOT ask for more information on optional parameters if it is not provided.

    Do not reflect on the quality of the returned search results in your response.
    """
    
    file_contents_prompt = "\n\nFile Contents:\n"
    for path, content in file_contents.items():
        file_contents_prompt += f"\n--- {path} ---\n{content}\n"
    
    if automode:
        iteration_info = ""
        if current_iteration is not None and max_iterations is not None:
            iteration_info = f"You are currently on iteration {current_iteration} out of {max_iterations} in automode."
        return BASE_SYSTEM_PROMPT + file_contents_prompt + "\n\n" + AUTOMODE_SYSTEM_PROMPT.format(iteration_info=iteration_info) + "\n\n" + chain_of_thought_prompt
    else:
        return BASE_SYSTEM_PROMPT + file_contents_prompt + "\n\n" + chain_of_thought_prompt

def create_folders(paths):
    """
    创建文件夹的函数。
    
    参数:
    paths (list): 要创建的文件夹路径列表。
    
    返回:
    str: 创建文件夹的结果信息。

    调用的外部函数:
    - os.makedirs(): 创建目录及其所有必要的父目录。
    """
    results = []
    for path in paths:
        try:
            # 创建文件夹，若已存在则不报错
            os.makedirs(path, exist_ok=True)
            results.append(f"Folder created: {path}")
        except Exception as e:
            results.append(f"Error creating folder {path}: {str(e)}")
    return "\n".join(results)

def create_files(files):
    """
    创建文件的函数。
    
    参数:
    files (list): 要创建的文件信息列表，每个文件包含路径和内容。
    
    返回:
    str: 创建文件的结果信息。

    调用的外部函数:
    - os.path.dirname(): 获取文件路径的目录部分。
    - os.makedirs(): 创建目录及其所有必要的父目录。
    """
    global file_contents
    results = []
    # 处理单个文件和多个文件的情况
    if isinstance(files, dict):
        files = [files]
    for file in files:
        try:
            path = file['path']  # 获取文件路径
            content = file['content']  # 获取文件内容
            dir_name = os.path.dirname(path)  # 获取文件所在目录
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)  # 创建目录
            with open(path, 'w') as f:
                f.write(content)  # 写入文件内容
            file_contents[path] = content  # 更新文件内容到全局字典
            results.append(f"File created and added to system prompt: {path}")
        except Exception as e:
            results.append(f"Error creating file {path}: {str(e)}")
    return "\n".join(results)

async def generate_edit_instructions(file_path, file_content, instructions, project_context, full_file_contents):
    """
    生成编辑指令的异步函数。
    
    参数:
    file_path (str): 文件路径。
    file_content (str): 文件内容。
    instructions (str): 编辑指令。
    project_context (str): 项目上下文。
    full_file_contents (dict): 所有文件的内容。
    
    返回:
    list: 生成的编辑指令列表。

    调用的外部函数:
    - client.beta.prompt_caching.messages.create(): 创建一个新的消息，使用提示缓存功能。
    - parse_search_replace_blocks(): 解析响应文本中的SEARCH/REPLACE块。
    - console.print(): 打印信息到控制台。
    """
    global code_editor_tokens, code_editor_memory, code_editor_files
    try:
        # 准备记忆上下文（这是唯一维护调用之间上下文的部分）
        memory_context = "\n".join([f"Memory {i+1}:\n{mem}" for i, mem in enumerate(code_editor_memory)])

        # 准备完整的文件内容上下文，排除正在编辑的文件（如果它已经在code_editor_files中）
        full_file_contents_context = "\n\n".join([
            f"--- {path} ---\n{content}" for path, content in full_file_contents.items()
            if path != file_path or path not in code_editor_files
        ])

        system_prompt = f"""
        You are an AI coding agent that generates edit instructions for code files. Your task is to analyze the provided code and generate SEARCH/REPLACE blocks for necessary changes. Follow these steps:

        1. Review the entire file content to understand the context:
        {file_content}

        2. Carefully analyze the specific instructions:
        {instructions}

        3. Take into account the overall project context:
        {project_context}

        4. Consider the memory of previous edits:
        {memory_context}

        5. Consider the full context of all files in the project:
        {full_file_contents_context}

        6. Generate SEARCH/REPLACE blocks for each necessary change. Each block should:
           - Include enough context to uniquely identify the code to be changed
           - Provide the exact replacement code, maintaining correct indentation and formatting
           - Focus on specific, targeted changes rather than large, sweeping modifications

        7. Ensure that your SEARCH/REPLACE blocks:
           - Address all relevant aspects of the instructions
           - Maintain or enhance code readability and efficiency
           - Consider the overall structure and purpose of the code
           - Follow best practices and coding standards for the language
           - Maintain consistency with the project context and previous edits
           - Take into account the full context of all files in the project

        IMPORTANT: RETURN ONLY THE SEARCH/REPLACE BLOCKS. NO EXPLANATIONS OR COMMENTS.
        USE THE FOLLOWING FORMAT FOR EACH BLOCK:

        <SEARCH>
        Code to be replaced
        </SEARCH>
        <REPLACE>
        New code to insert
        </REPLACE>

        If no changes are needed, return an empty list.
        """

        response = client.beta.prompt_caching.messages.create(
            model=CODEEDITORMODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            messages=[
                {"role": "user", "content": "Generate SEARCH/REPLACE blocks for the necessary changes."}
            ],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        # 更新代码编辑器的token使用情况
        code_editor_tokens['input'] += response.usage.input_tokens
        code_editor_tokens['output'] += response.usage.output_tokens
        code_editor_tokens['cache_creation'] = response.usage.cache_creation_input_tokens
        code_editor_tokens['cache_read'] = response.usage.cache_read_input_tokens

        # 解析响应以提取SEARCH/REPLACE块
        edit_instructions = parse_search_replace_blocks(response.content[0].text)

        # 更新代码编辑器记忆（这是唯一维护调用之间上下文的部分）
        code_editor_memory.append(f"Edit Instructions for {file_path}:\n{response.content[0].text}")

        # 将文件添加到code_editor_files集合
        code_editor_files.add(file_path)

        return edit_instructions

    except Exception as e:
        console.print(f"Error in generating edit instructions: {str(e)}", style="bold red")
        return []  # 如果发生任何异常，则返回空列表

def parse_search_replace_blocks(response_text, use_fuzzy=USE_FUZZY_SEARCH):
    """
    解析响应文本中的SEARCH/REPLACE块。

    参数:
    response_text (str): 包含SEARCH/REPLACE块的文本。
    use_fuzzy (bool): 是否对搜索块使用模糊匹配。

    返回:
    list: 包含'search'、'replace'和'similarity'键的字典列表。

    调用的外部函数:
    - re.findall(): 查找所有匹配的子字符串。
    - difflib.get_close_matches(): 查找最接近的匹配。
    - difflib.SequenceMatcher().ratio(): 计算两个序列的相似度。
    """
    blocks = []
    pattern = r'<SEARCH>\s*(.*?)\s*</SEARCH>\s*<REPLACE>\s*(.*?)\s*</REPLACE>'
    matches = re.findall(pattern, response_text, re.DOTALL)
    
    for search, replace in matches:
        search = search.strip()  # 去除搜索内容的前后空格
        replace = replace.strip()  # 去除替换内容的前后空格
        similarity = 1.0  # 默认相似度为完全匹配

        if use_fuzzy and search not in response_text:
            # 在这里实现模糊匹配逻辑
            best_match = difflib.get_close_matches(search, [response_text], n=1, cutoff=0.6)
            if best_match:
                similarity = difflib.SequenceMatcher(None, search, best_match[0]).ratio()  # 计算相似度
            else:
                similarity = 0.0  # 如果没有找到最佳匹配，则相似度为0

        blocks.append({
            'search': search,
            'replace': replace,
            'similarity': similarity
        })
    
    return blocks

async def edit_and_apply_multiple(files, project_context, is_automode=False, max_retries=3):
    """
    异步编辑并应用多个文件的函数。
    
    参数:
    files (list): 要编辑的文件列表，每个文件包含路径和指令。
    project_context (str): 项目上下文。
    is_automode (bool): 是否在自动模式下。
    max_retries (int): 最大重试次数。
    
    返回:
    tuple: 包含结果信息和控制台输出的元组。

    调用的外部函数:
    - generate_edit_instructions(): 生成编辑指令。
    - apply_edits(): 应用编辑指令。
    - console.print(): 打印信息到控制台。
    """
    global file_contents
    results = []
    console_outputs = []
    for file in files:
        path = file['path']  # 获取文件路径
        instructions = file['instructions']  # 获取编辑指令
        try:
            original_content = file_contents.get(path, "")  # 获取原始内容
            if not original_content:
                with open(path, 'r') as f:
                    original_content = f.read()  # 读取文件内容
                file_contents[path] = original_content  # 更新文件内容到全局字典

            for attempt in range(max_retries):
                # 生成编辑指令
                edit_instructions = await generate_edit_instructions(path, original_content, instructions, project_context, file_contents)

                if edit_instructions:
                    console.print(Panel(f"File: {path}\nAttempt {attempt + 1}/{max_retries}: The following SEARCH/REPLACE blocks have been generated:", title="Edit Instructions", style="cyan"))
                    for i, block in enumerate(edit_instructions, 1):
                        console.print(f"Block {i}:")
                        console.print(Panel(f"SEARCH:\n{block['search']}\n\nREPLACE:\n{block['replace']}\nSimilarity: {block['similarity']:.2f}", expand=False))

                    # 应用编辑
                    edited_content, changes_made, failed_edits, console_output = await apply_edits(path, edit_instructions, original_content)
                    console_outputs.append(console_output)

                    if changes_made:
                        file_contents[path] = edited_content  # 更新文件内容
                        console.print(Panel(f"File contents updated in system prompt: {path}", style="green"))

                        if failed_edits:
                            console.print(Panel(f"Some edits could not be applied to {path}. Retrying...", style="yellow"))
                            instructions += f"\n\nPlease retry the following edits that could not be applied:\n{failed_edits}"
                            original_content = edited_content  # 更新原始内容
                            continue

                        results.append(f"Changes applied to {path}")  # 记录成功应用的更改
                        break
                    elif attempt == max_retries - 1:
                        results.append(f"No changes could be applied to {path} after {max_retries} attempts. Please review the edit instructions and try again.")
                    else:
                        console.print(Panel(f"No changes could be applied to {path} in attempt {attempt + 1}. Retrying...", style="yellow"))
                else:
                    results.append(f"No changes suggested for {path}")  # 没有建议的更改
                    break

            if attempt == max_retries - 1:
                results.append(f"Failed to apply changes to {path} after {max_retries} attempts.")  # 记录失败信息
        except Exception as e:
            error_message = f"Error editing/applying to file {path}: {str(e)}"  # 记录错误信息
            results.append(error_message)
            console_outputs.append(error_message)

    return "\n".join(results), "\n".join(console_outputs)

async def apply_edits(file_path, edit_instructions, original_content):
    """
    应用编辑指令的异步函数。
    
    参数:
    file_path (str): 文件路径。
    edit_instructions (list): 编辑指令列表。
    original_content (str): 原始文件内容。
    
    返回:
    tuple: 包含编辑后的内容、是否有更改、失败的编辑和控制台输出的元组。

    调用的外部函数:
    - re.compile(): 编译正则表达式模式。
    - re.escape(): 转义正则表达式中的特殊字符。
    - re.sub(): 替换字符串中的匹配项。
    - difflib.get_close_matches(): 查找最接近的匹配。
    - generate_diff(): 生成原始内容和新内容之间的差异。
    - console.print(): 打印信息到控制台。
    - Progress(): 创建进度条。
    """
    changes_made = False  # 记录是否有更改
    edited_content = original_content  # 初始化编辑后的内容
    total_edits = len(edit_instructions)  # 获取总编辑数
    failed_edits = []  # 记录失败的编辑
    console_output = []  # 控制台输出

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:
        edit_task = progress.add_task("[cyan]Applying edits...", total=total_edits)  # 添加进度任务

        for i, edit in enumerate(edit_instructions, 1):
            search_content = edit['search'].strip()  # 获取搜索内容并去除空格
            replace_content = edit['replace'].strip()  # 获取替换内容并去除空格
            similarity = edit['similarity']  # 获取相似度

            # 使用正则表达式查找内容，忽略前后空格
            pattern = re.compile(re.escape(search_content), re.DOTALL)
            match = pattern.search(edited_content)
            
            if match or (USE_FUZZY_SEARCH and similarity >= 0.8):
                if not match:
                    # 如果使用模糊搜索且没有精确匹配，找到最佳匹配
                    best_match = difflib.get_close_matches(search_content, [edited_content], n=1, cutoff=0.6)
                    if best_match:
                        match = re.search(re.escape(best_match[0]), edited_content)

                if match:
                    # 替换内容，保留原始空格
                    start, end = match.span()
                    # 去除<SEARCH>和<REPLACE>标签
                    replace_content_cleaned = re.sub(r'</?SEARCH>|</?REPLACE>', '', replace_content)
                    edited_content = edited_content[:start] + replace_content_cleaned + edited_content[end:]
                    changes_made = True  # 标记已更改

                    # 显示此编辑的差异
                    diff_result = generate_diff(search_content, replace_content, file_path)
                    console.print(Panel(diff_result, title=f"Changes in {file_path} ({i}/{total_edits}) - Similarity: {similarity:.2f}", style="cyan"))
                    console_output.append(f"Edit {i}/{total_edits} applied successfully")  # 记录成功应用的编辑
                else:
                    message = f"Edit {i}/{total_edits} not applied: content not found (Similarity: {similarity:.2f})"  # 记录未应用的编辑
                    console_output.append(message)
                    console.print(Panel(message, style="yellow"))
                    failed_edits.append(f"Edit {i}: {search_content}")  # 记录失败的编辑
            else:
                message = f"Edit {i}/{total_edits} not applied: content not found (Similarity: {similarity:.2f})"  # 记录未应用的编辑
                console_output.append(message)
                console.print(Panel(message, style="yellow"))
                failed_edits.append(f"Edit {i}: {search_content}")  # 记录失败的编辑

            progress.update(edit_task, advance=1)  # 更新进度

    if not changes_made:
        message = "No changes were applied. The file content already matches the desired state."  # 没有应用更改
        console_output.append(message)
        console.print(Panel(message, style="green"))
    else:
        # 将更改写入文件
        with open(file_path, 'w') as file:
            file.write(edited_content)
        message = f"Changes have been written to {file_path}"  # 记录已写入的更改
        console_output.append(message)
        console.print(Panel(message, style="green"))

    return edited_content, changes_made, "\n".join(failed_edits), "\n".join(console_output)

def highlight_diff(diff_text):
    """
    高亮显示差异文本的函数。
    
    参数:
    diff_text (str): 差异文本。
    
    返回:
    Syntax: 高亮显示的差异文本对象。

    调用的外部函数:
    - Syntax(): 创建一个语法高亮对象。
    """
    # 使用Rich库的Syntax高亮显示差异文本
    return Syntax(diff_text, "diff", theme="monokai", line_numbers=True)

def generate_diff(original, new, path):
    """
    生成原始内容和新内容之间的差异的函数。
    
    参数:
    original (str): 原始内容。
    new (str): 新内容。
    path (str): 文件路径。
    
    返回:
    Syntax: 高亮显示的差异文本对象。

    调用的外部函数:
    - difflib.unified_diff(): 生成统一格式的差异。
    - highlight_diff(): 高亮显示差异文本。
    """
    # 生成原始内容和新内容之间的差异
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3
    ))

    diff_text = ''.join(diff)  # 将差异列表转换为字符串
    highlighted_diff = highlight_diff(diff_text)  # 高亮显示差异

    return highlighted_diff

async def execute_code(code, timeout=10):
    """
    执行Python代码的异步函数。
    
    参数:
    code (str): 要执行的Python代码。
    timeout (int): 超时时间，默认为10秒。
    
    返回:
    tuple: 包含进程ID和执行结果的元组。

    调用的外部函数:
    - setup_virtual_environment(): 设置虚拟环境。
    - asyncio.create_subprocess_shell(): 创建一个子进程来运行shell命令。
    - asyncio.wait_for(): 等待一个协程完成，有超时限制。
    """
    global running_processes
    venv_path, activate_script = setup_virtual_environment()  # 设置虚拟环境
    
    # 生成此进程的唯一标识符
    process_id = f"process_{len(running_processes)}"
    
    # 将代码写入临时文件
    with open(f"{process_id}.py", "w") as f:
        f.write(code)
    
    # 准备运行代码的命令
    if sys.platform == "win32":
        command = f'"{activate_script}" && python3 {process_id}.py'
    else:
        command = f'source "{activate_script}" && python3 {process_id}.py'
    
    # 创建一个进程来运行命令
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        shell=True,
        preexec_fn=None if sys.platform == "win32" else os.setsid
    )
    
    # 将进程存储在全局字典中
    running_processes[process_id] = process
    
    try:
        # 等待初始输出或超时
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        stdout = stdout.decode()  # 解码标准输出
        stderr = stderr.decode()  # 解码标准错误
        return_code = process.returncode  # 获取返回码
    except asyncio.TimeoutError:
        # 如果超时，表示进程仍在运行
        stdout = "Process started and running in the background."
        stderr = ""
        return_code = "Running"
    
    execution_result = f"Process ID: {process_id}\n\nStdout:\n{stdout}\n\nStderr:\n{stderr}\n\nReturn Code: {return_code}"
    return process_id, execution_result

def read_file(path):
    """
    读取指定路径文件内容的函数。
    
    参数:
    path (str): 文件路径。
    
    返回:
    str: 读取结果信息。

    调用的外部函数:
    - open(): 打开文件。
    """
    global file_contents
    try:
        with open(path, 'r') as f:
            content = f.read()  # 读取文件内容
        file_contents[path] = content  # 更新文件内容到全局字典
        return f"File '{path}' has been read and stored in the system prompt."  # 返回成功信息
    except Exception as e:
        return f"Error reading file: {str(e)}"  # 返回错误信息

def read_multiple_files(paths):
    """
    读取多个文件内容的函数。
    
    参数:
    paths (list): 文件路径列表。
    
    返回:
    str: 读取结果信息。

    调用的外部函数:
    - open(): 打开文件。
    """
    global file_contents
    results = []
    for path in paths:
        try:
            with open(path, 'r') as f:
                content = f.read()  # 读取文件内容
            file_contents[path] = content  # 更新文件内容到全局字典
            results.append(f"File '{path}' has been read and stored in the system prompt.")  # 返回成功信息
        except Exception as e:
            results.append(f"Error reading file '{path}': {str(e)}")  # 返回错误信息
    return "\n".join(results)


def list_files(path="."):
    """
    列出指定路径下所有文件的函数。
    
    参数:
    path (str): 目录路径，默认为当前目录。
    
    返回:
    str: 文件列表或错误信息。

    调用的外部函数:
    - os.listdir(): 返回指定目录下的文件和子目录列表。
    """
    try:
        files = os.listdir(path)  # 列出指定路径下的所有文件
        return "\n".join(files)  # 返回文件列表
    except Exception as e:
        return f"Error listing files: {str(e)}"  # 返回错误信息

def tavily_search(query):
    """
    使用Tavily API进行搜索的函数。
    
    参数:
    query (str): 搜索查询。
    
    返回:
    str: 搜索结果或错误信息。

    调用的外部函数:
    - tavily.qna_search(): 使用Tavily API进行问答搜索。
    """
    try:
        response = tavily.qna_search(query=query, search_depth="advanced")  # 使用Tavily API进行搜索
        return response  # 返回搜索结果
    except Exception as e:
        return f"Error performing search: {str(e)}"  # 返回错误信息

def stop_process(process_id):
    """
    停止指定进程的函数。
    
    参数:
    process_id (str): 进程ID。
    
    返回:
    str: 停止进程的结果信息。

    调用的外部函数:
    - os.killpg(): 向进程组发送信号（在非Windows系统上）。
    """
    global running_processes
    if process_id in running_processes:
        process = running_processes[process_id]  # 获取进程
        if sys.platform == "win32":
            process.terminate()  # 终止进程
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)  # 发送终止信号
        del running_processes[process_id]  # 从全局字典中删除进程
        return f"Process {process_id} has been stopped."  # 返回成功信息
    else:
        return f"No running process found with ID {process_id}."  # 返回错误信息

tools = [
    {
        "name": "create_folders",
        "description": "Create new folders at the specified paths. This tool should be used when you need to create one or more directories in the project structure. It will create all necessary parent directories if they don't exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "An array of absolute or relative paths where the folders should be created. Use forward slashes (/) for path separation, even on Windows systems."
                }
            },
            "required": ["paths"]
        }
    },
    {
        "name": "create_files",
        "description": "Create one or more new files with the given contents. This tool should be used when you need to create files in the project structure. It will create all necessary parent directories if they don't exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The absolute or relative path where the file should be created. Use forward slashes (/) for path separation, even on Windows systems."
                            },
                            "content": {
                                "type": "string",
                                "description": "The content of the file. This should include all necessary code, comments, and formatting."
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            }
        }
    },
    {
        "name": "edit_and_apply_multiple",
        "description": "Apply AI-powered improvements to multiple files based on specific instructions and detailed project context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The absolute or relative path of the file to edit."
                            },
                            "instructions": {
                                "type": "string",
                                "description": "Specific instructions for editing this file."
                            }
                        },
                        "required": ["path", "instructions"]
                    }
                },
                "project_context": {
                    "type": "string",
                    "description": "Comprehensive context about the project, including recent changes, new variables or functions, interconnections between files, coding standards, and any other relevant information that might affect the edits."
                }
            },
            "required": ["files", "project_context"]
        }
    },
    {
        "name": "execute_code",
        "description": "Execute Python code in the 'code_execution_env' virtual environment and return the output. This tool should be used when you need to run code and see its output or check for errors. All code execution happens exclusively in this isolated environment. The tool will return the standard output, standard error, and return code of the executed code. Long-running processes will return a process ID for later management.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code to execute in the 'code_execution_env' virtual environment. Include all necessary imports and ensure the code is complete and self-contained."
                }
            },
            "required": ["code"]
        }
    },
    {
        "name": "stop_process",
        "description": "Stop a running process by its ID. This tool should be used to terminate long-running processes that were started by the execute_code tool. It will attempt to stop the process gracefully, but may force termination if necessary. The tool will return a success message if the process is stopped, and an error message if the process doesn't exist or can't be stopped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "process_id": {
                    "type": "string",
                    "description": "The ID of the process to stop, as returned by the execute_code tool for long-running processes."
                }
            },
            "required": ["process_id"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file at the specified path. This tool should be used when you need to examine the contents of an existing file. It will return the entire contents of the file as a string. If the file doesn't exist or can't be read, an appropriate error message will be returned.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The absolute or relative path of the file to read. Use forward slashes (/) for path separation, even on Windows systems."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "read_multiple_files",
        "description": "Read the contents of multiple files at the specified paths. This tool should be used when you need to examine the contents of multiple existing files at once. It will return the status of reading each file, and store the contents of successfully read files in the system prompt. If a file doesn't exist or can't be read, an appropriate error message will be returned for that file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "An array of absolute or relative paths of the files to read. Use forward slashes (/) for path separation, even on Windows systems."
                }
            },
            "required": ["paths"]
        }
    },
    {
        "name": "list_files",
        "description": "List all files and directories in the specified folder. This tool should be used when you need to see the contents of a directory. It will return a list of all files and subdirectories in the specified path. If the directory doesn't exist or can't be read, an appropriate error message will be returned.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The absolute or relative path of the folder to list. Use forward slashes (/) for path separation, even on Windows systems. If not provided, the current working directory will be used."
                }
            }
        }
    },
    {
        "name": "tavily_search",
        "description": "Perform a web search using the Tavily API to get up-to-date information or additional context. This tool should be used when you need current information or feel a search could provide a better answer to the user's query. It will return a summary of the search results, including relevant snippets and source URLs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Be as specific and detailed as possible to get the most relevant results."
                }
            },
            "required": ["query"]
        }
    }
]

from typing import Dict, Any

async def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行指定工具的异步函数。
    
    参数:
    tool_name (str): 工具名称。
    tool_input (dict): 工具输入参数。
    
    返回:
    dict: 包含工具执行结果的字典。

    调用的外部函数:
    - create_files(): 创建文件。
    - edit_and_apply_multiple(): 编辑并应用多个文件的更改。
    - create_folders(): 创建文件夹。
    - read_file(): 读取文件内容。
    - read_multiple_files(): 读取多个文件的内容。
    - list_files(): 列出指定目录下的文件和文件夹。
    - tavily_search(): 执行网络搜索。
    - stop_process(): 停止指定的进程。
    - execute_code(): 执行代码并返回结果。
    - send_to_ai_for_executing(): 发送代码执行结果给AI进行分析。
    - logging.error(): 记录错误日志。
    """
    try:
        result = None
        is_error = False
        console_output = None

        if tool_name == "create_files":
            # 处理单个文件和多个文件的情况
            files = tool_input.get("files", [tool_input])
            result = create_files(files)  # 创建文件
        elif tool_name == "edit_and_apply_multiple":
            result, console_output = await edit_and_apply_multiple(tool_input["files"], tool_input["project_context"], is_automode=automode)  # 编辑并应用多个文件
        elif tool_name == "create_folders":
            result = create_folders(tool_input["paths"])  # 创建文件夹
        elif tool_name == "read_file":
            result = read_file(tool_input["path"])  # 读取文件
        elif tool_name == "read_multiple_files":
            result = read_multiple_files(tool_input["paths"])  # 读取多个文件
        elif tool_name == "list_files":
            result = list_files(tool_input.get("path", "."))  # 列出文件
        elif tool_name == "tavily_search":
            result = tavily_search(tool_input["query"])  # 执行搜索
        elif tool_name == "stop_process":
            result = stop_process(tool_input["process_id"])  # 停止进程
        elif tool_name == "execute_code":
            process_id, execution_result = await execute_code(tool_input["code"])  # 执行代码
            analysis_task = asyncio.create_task(send_to_ai_for_executing(tool_input["code"], execution_result))  # 发送执行结果进行分析
            analysis = await analysis_task
            result = f"{execution_result}\n\nAnalysis:\n{analysis}"  # 返回执行结果和分析
            if process_id in running_processes:
                result += "\n\nNote: The process is still running in the background."  # 进程仍在运行的提示
        else:
            is_error = True
            result = f"Unknown tool: {tool_name}"  # 未知工具的错误信息

        return {
            "content": result,
            "is_error": is_error,
            "console_output": console_output
        }
    except KeyError as e:
        logging.error(f"Missing required parameter {str(e)} for tool {tool_name}")  # 记录缺少参数的错误
        return {
            "content": f"Error: Missing required parameter {str(e)} for tool {tool_name}",
            "is_error": True,
            "console_output": None
        }
    except Exception as e:
        logging.error(f"Error executing tool {tool_name}: {str(e)}")  # 记录执行工具的错误
        return {
            "content": f"Error executing tool {tool_name}: {str(e)}",
            "is_error": True,
            "console_output": None
        }

def encode_image_to_base64(image_path):
    """
    将图像文件编码为Base64格式的函数。
    
    参数:
    image_path (str): 图像文件的路径。
    
    返回:
    str: 图像的Base64编码或错误信息。

    调用的外部函数:
    - Image.open(): 打开图像文件。
    - Image.thumbnail(): 调整图像大小。
    - Image.convert(): 转换图像模式。
    - io.BytesIO(): 创建字节流对象。
    - base64.b64encode(): 将二进制数据编码为Base64格式。
    """
    try:
        with Image.open(image_path) as img:  # 打开图像文件
            max_size = (1024, 1024)  # 设置最大尺寸
            img.thumbnail(max_size, Image.DEFAULT_STRATEGY)  # 调整图像大小
            if img.mode != 'RGB':
                img = img.convert('RGB')  # 转换为RGB模式
            img_byte_arr = io.BytesIO()  # 创建字节流
            img.save(img_byte_arr, format='JPEG')  # 保存图像为JPEG格式
            return base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')  # 返回图像的Base64编码
    except Exception as e:
        return f"Error encoding image: {str(e)}"  # 返回编码图像的错误信息

async def send_to_ai_for_executing(code, execution_result):
    """
    发送代码和执行结果进行分析的异步函数。
    
    参数:
    code (str): 要分析的代码。
    execution_result (str): 执行结果。
    
    返回:
    str: 分析结果。

    调用的外部函数:
    - client.beta.prompt_caching.messages.create(): 创建AI消息并获取响应。
    - console.print(): 打印控制台信息。
    """
    global code_execution_tokens

    try:
        system_prompt = f"""
        You are an AI code execution agent. Your task is to analyze the provided code and its execution result from the 'code_execution_env' virtual environment, then provide a concise summary of what worked, what didn't work, and any important observations. Follow these steps:

        1. Review the code that was executed in the 'code_execution_env' virtual environment:
        {code}

        2. Analyze the execution result from the 'code_execution_env' virtual environment:
        {execution_result}

        3. Provide a brief summary of:
           - What parts of the code executed successfully in the virtual environment
           - Any errors or unexpected behavior encountered in the virtual environment
           - Potential improvements or fixes for issues, considering the isolated nature of the environment
           - Any important observations about the code's performance or output within the virtual environment
           - If the execution timed out, explain what this might mean (e.g., long-running process, infinite loop)

        Be concise and focus on the most important aspects of the code execution within the 'code_execution_env' virtual environment.

        IMPORTANT: PROVIDE ONLY YOUR ANALYSIS AND OBSERVATIONS. DO NOT INCLUDE ANY PREFACING STATEMENTS OR EXPLANATIONS OF YOUR ROLE.
        """

        response = client.beta.prompt_caching.messages.create(
            model=CODEEXECUTIONMODEL,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            messages=[
                {"role": "user", "content": f"Analyze this code execution from the 'code_execution_env' virtual environment:\n\nCode:\n{code}\n\nExecution Result:\n{execution_result}"}
            ],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )

        # 更新代码执行的token使用情况
        code_execution_tokens['input'] += response.usage.input_tokens
        code_execution_tokens['output'] += response.usage.output_tokens
        code_execution_tokens['cache_creation'] = response.usage.cache_creation_input_tokens
        code_execution_tokens['cache_read'] = response.usage.cache_read_input_tokens

        analysis = response.content[0].text  # 获取分析结果

        return analysis

    except Exception as e:
        console.print(f"Error in AI code execution analysis: {str(e)}", style="bold red")  # 记录分析错误
        return f"Error analyzing code execution from 'code_execution_env': {str(e)}"  # 返回分析错误信息

def save_chat(format):
    """
    保存聊天记录的函数。
    
    参数:
    format (str): 保存格式（Markdown或JSON）。
    
    返回:
    str: 保存的文件名。

    调用的外部函数:
    - datetime.now(): 获取当前日期和时间。
    - json.dump(): 将Python对象序列化为JSON格式。
    """
    # 生成文件名
    now = datetime.datetime.now()
    filename = f"Chat_{now.strftime('%H%M')}.{'md' if format == 'markdown' else 'json'}"
    
    if format == 'markdown':
        # 将对话历史格式化为Markdown
        formatted_chat = "# Claude-3-Sonnet Engineer Chat Log\n\n"
        for message in conversation_history:
            if message['role'] == 'user':
                formatted_chat += f"## User\n\n{message['content']}\n\n"
            elif message['role'] == 'assistant':
                if isinstance(message['content'], str):
                    formatted_chat += f"## Claude\n\n{message['content']}\n\n"
                elif isinstance(message['content'], list):
                    for content in message['content']:
                        if content['type'] == 'tool_use':
                            formatted_chat += f"### Tool Use: {content['name']}\n\n```json\n{json.dumps(content['input'], indent=2)}\n```\n\n"
                        elif content['type'] == 'text':
                            formatted_chat += f"## Claude\n\n{content['text']}\n\n"
            elif message['role'] == 'user' and isinstance(message['content'], list):
                for content in message['content']:
                    if content['type'] == 'tool_result':
                        formatted_chat += f"### Tool Result\n\n```\n{content['content']}\n```\n\n"

        # 保存到文件
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(formatted_chat)
    else:
        # 保存为JSON格式
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(conversation_history, f, indent=2, ensure_ascii=False)
    
    return filename

def load_chat(filename):
    """
    加载聊天记录的函数。
    
    参数:
    filename (str): 聊天记录文件名。
    
    返回:
    bool: 加载是否成功。

    调用的外部函数:
    - json.load(): 从JSON文件中加载数据。
    - console.print(): 打印控制台信息。
    - display_token_usage(): 显示token使用情况。
    """
    global conversation_history, main_model_tokens, tool_checker_tokens, code_editor_tokens, code_execution_tokens
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)  # 加载聊天记录

        # 验证加载的数据结构
        if not isinstance(loaded_data, list) or not all(isinstance(item, dict) for item in loaded_data):
            raise ValueError("Invalid chat file format")  # 抛出格式错误

        conversation_history = loaded_data  # 更新对话历史

        # 重置token计数
        main_model_tokens = {'input': 0, 'output': 0}
        tool_checker_tokens = {'input': 0, 'output': 0}
        code_editor_tokens = {'input': 0, 'output': 0}
        code_execution_tokens = {'input': 0, 'output': 0}

        console.print(Panel(f"Chat loaded from {filename}", title="Chat Loaded", style="bold green"))  # 显示加载成功信息
        console.print(Panel("Token usage information will be recalculated.", title="Recalculation", style="bold yellow"))  # 显示token信息将被重新计算
        display_token_usage()  # 显示token使用情况
        return True
    except FileNotFoundError:
        console.print(Panel(f"File not found: {filename}", title="Error", style="bold red"))  # 显示文件未找到错误
    except json.JSONDecodeError:
        console.print(Panel(f"Invalid JSON in file: {filename}", title="Error", style="bold red"))  # 显示JSON格式错误
    except Exception as e:
        console.print(Panel(f"Error loading chat: {str(e)}", title="Error", style="bold red"))  # 显示加载错误
    return False

async def chat_with_claude(user_input, image_path=None, current_iteration=None, max_iterations=None):
    """
    与Claude进行对话的异步函数。
    
    参数:
    user_input (str): 用户输入的内容。
    image_paonal[str]): 图像文件路径（如果有）。
    current_iterationth (Opti (Optional[int]): 当前迭代次数。
    max_iterations (Optional[int]): 最大迭代次数。
    
    返回:
    tuple: 包含助手响应和退出标志的元组。

    调用的外部函数:
    - encode_image_to_base64(): 将图像编码为Base64格式。
    - update_system_prompt(): 更新系统提示。
    - json.dumps(): 将Python对象转换为JSON字符串。
    - client.beta.prompt_caching.messages.create(): 创建一个新的AI消息。
    - execute_tool(): 执行指定的工具。
    - console.print(): 打印控制台信息。
    - display_token_usage(): 显示token使用情况。
    """
    global conversation_history, automode, main_model_tokens

    current_conversation = []  # 当前对话记录

    if image_path:
        console.print(Panel(f"Processing image at path: {image_path}", title_align="left", title="Image Processing", expand=False, style="yellow"))  # 显示图像处理信息
        image_base64 = encode_image_to_base64(image_path)  # 将图像编码为Base64

        if image_base64.startswith("Error"):
            console.print(Panel(f"Error encoding image: {image_base64}", title="Error", style="bold red"))  # 显示编码错误
            return "I'm sorry, there was an error processing the image. Please try again.", False

        image_message = {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_base64  # 图像数据
                    }
                },
                {
                    "type": "text",
                    "text": f"User input for image: {user_input}"  # 用户输入的文本
                }
            ]
        }
        current_conversation.append(image_message)  # 添加图像消息到当前对话
        console.print(Panel("Image message added to conversation history", title_align="left", title="Image Added", style="green"))  # 显示图像已添加信息
    else:
        current_conversation.append({"role": "user", "content": user_input})  # 添加用户输入到当前对话

    # 过滤对话历史以维护上下文
    filtered_conversation_history = []
    for message in conversation_history:
        if isinstance(message['content'], list):
            filtered_content = [
                content for content in message['content']
                if content.get('type') != 'tool_result' or (
                    content.get('type') == 'tool_result' and
                    not any(keyword in content.get('output', '') for keyword in [
                        "File contents updated in system prompt",
                        "File created and added to system prompt",
                        "has been read and stored in the system prompt"
                    ])
                )
            ]
            if filtered_content:
                filtered_conversation_history.append({**message, 'content': filtered_content})  # 添加过滤后的消息
        else:
            filtered_conversation_history.append(message)  # 添加非列表消息

    # 将过滤后的历史与当前对话结合以维护上下文
    messages = filtered_conversation_history + current_conversation

    try:
        # MAINMODEL调用，使用提示缓存
        response = client.beta.prompt_caching.messages.create(
            model=MAINMODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": update_system_prompt(current_iteration, max_iterations),  # 更新系统提示
                    "cache_control": {"type": "ephemeral"}
                },
                {
                    "type": "text",
                    "text": json.dumps(tools),  # 将工具信息转换为JSON格式
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            messages=messages,  # 发送的消息
            tools=tools,  # 可用工具
            tool_choice={"type": "auto"},  # 自动选择工具
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        # 更新MAINMODEL的token使用情况
        main_model_tokens['input'] += response.usage.input_tokens
        main_model_tokens['output'] += response.usage.output_tokens
        main_model_tokens['cache_creation'] = response.usage.cache_creation_input_tokens
        main_model_tokens['cache_read'] = response.usage.cache_read_input_tokens
    except APIStatusError as e:
        if e.status_code == 429:
            console.print(Panel("Rate limit exceeded. Retrying after a short delay...", title="API Error", style="bold yellow"))  # 显示速率限制错误
            time.sleep(5)  # 等待5秒后重试
            return await chat_with_claude(user_input, image_path, current_iteration, max_iterations)
        else:
            console.print(Panel(f"API Error: {str(e)}", title="API Error", style="bold red"))  # 显示API错误
            return "I'm sorry, there was an error communicating with the AI. Please try again.", False
    except APIError as e:
        console.print(Panel(f"API Error: {str(e)}", title="API Error", style="bold red"))  # 显示API错误
        return "I'm sorry, there was an error communicating with the AI. Please try again.", False

    assistant_response = ""  # 初始化助手响应
    exit_continuation = False  # 初始化退出标志
    tool_uses = []  # 初始化工具使用记录

    for content_block in response.content:
        if content_block.type == "text":
            assistant_response += content_block.text  # 添加文本响应
            if CONTINUATION_EXIT_PHRASE in content_block.text:
                exit_continuation = True  # 设置退出标志
        elif content_block.type == "tool_use":
            tool_uses.append(content_block)  # 添加工具使用记录

    console.print(Panel(Markdown(assistant_response), title="Claude's Response", title_align="left", border_style="blue", expand=False))  # 显示助手响应

    # 显示上下文中的文件
    if file_contents:
        files_in_context = "\n".join(file_contents.keys())  # 获取上下文中的文件列表
    else:
        files_in_context = "No files in context. Read, create, or edit files to add."  # 没有文件的提示
    console.print(Panel(files_in_context, title="Files in Context", title_align="left", border_style="white", expand=False))  # 显示上下文文件

    for tool_use in tool_uses:
        tool_name = tool_use.name  # 获取工具名称
        tool_input = tool_use.input  # 获取工具输入
        tool_use_id = tool_use.id  # 获取工具使用ID

        console.print(Panel(f"Tool Used: {tool_name}", style="green"))  # 显示使用的工具
        console.print(Panel(f"Tool Input: {json.dumps(tool_input, indent=2)}", style="green"))  # 显示工具输入

        tool_result = await execute_tool(tool_name, tool_input)  # 执行工具
        
        if tool_result["is_error"]:
            console.print(Panel(tool_result["content"], title="Tool Execution Error", style="bold red"))  # 显示工具执行错误
        else:
            console.print(Panel(tool_result["content"], title_align="left", title="Tool Result", style="green"))  # 显示工具结果

        current_conversation.append({
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": tool_name,
                    "input": tool_input
                }
            ]
        })

        current_conversation.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": tool_result["content"],
                    "is_error": tool_result["is_error"]
                }
            ]
        })

        # 如果适用，更新file_contents字典
        if tool_name in ['create_files', 'edit_and_apply_multiple', 'read_file', 'read_multiple_files'] and not tool_result["is_error"]:
            if tool_name == 'create_files':
                for file in tool_input['files']:
                    if "File created and added to system prompt" in tool_result["content"]:
                        file_contents[file['path']] = file['content']  # 更新文件内容
            elif tool_name == 'edit_and_apply_multiple':
                for file in tool_input['files']:
                    if f"Changes applied to {file['path']}" in tool_result["content"]:
                        # file_contents字典已在edit_and_apply_multiple函数中更新
                        pass
            elif tool_name == 'read_file':
                if "has been read and stored in the system prompt" in tool_result["content"]:
                    # file_contents字典已在read_file函数中更新
                    pass
            elif tool_name == 'read_multiple_files':
                # file_contents字典已在read_multiple_files函数中更新
                pass

        messages = filtered_conversation_history + current_conversation  # 更新消息记录

        try:
            tool_response = client.messages.create(
                model=TOOLCHECKERMODEL,
                max_tokens=4096,
                system=update_system_prompt(current_iteration, max_iterations),  # 更新系统提示
                extra_headers={"anthropic-beta": "max-tokens-3-5-sonnet-2024-07-15"},
                messages=messages,  # 发送的消息
                tools=tools,  # 可用工具
                tool_choice={"type": "auto"}  # 自动选择工具
            )
            # 更新工具检查器的token使用情况
            tool_checker_tokens['input'] += tool_response.usage.input_tokens
            tool_checker_tokens['output'] += tool_response.usage.output_tokens

            tool_checker_response = ""  # 初始化工具检查器响应
            for tool_content_block in tool_response.content:
                if tool_content_block.type == "text":
                    tool_checker_response += tool_content_block.text  # 添加工具检查器响应文本
            console.print(Panel(Markdown(tool_checker_response), title="Claude's Response to Tool Result",  title_align="left", border_style="blue", expand=False))  # 显示工具检查器响应
            assistant_response += "\n\n" + tool_checker_response  # 添加工具检查器响应到助手响应
        except APIError as e:
            error_message = f"Error in tool response: {str(e)}"  # 记录工具响应错误
            console.print(Panel(error_message, title="Error", style="bold red"))  # 显示错误信息
            assistant_response += f"\n\n{error_message}"  # 添加错误信息到助手响应

    if assistant_response:
        current_conversation.append({"role": "assistant", "content": assistant_response})  # 添加助手响应到当前对话

    conversation_history = messages + [{"role": "assistant", "content": assistant_response}]  # 更新对话历史

    # 显示token使用情况
    display_token_usage()

    return assistant_response, exit_continuation  # 返回助手响应和退出标志

def reset_code_editor_memory():
    """
    重置代码编辑器记忆的函数。

    调用的外部函数:
    - console.print(): 在控制台打印信息。
    - Panel(): 创建一个格式化的面板，用于显示信息。
    """
    global code_editor_memory
    code_editor_memory = []  # 重置代码编辑器记忆
    console.print(Panel("Code editor memory has been reset.", title="Reset", style="bold green"))  # 显示重置信息

def reset_conversation():
    """
    重置对话历史和相关状态的函数。

    调用的外部函数:
    - console.print(): 在控制台打印信息。
    - Panel(): 创建一个格式化的面板，用于显示信息。
    - reset_code_editor_memory(): 重置代码编辑器记忆。
    - display_token_usage(): 显示token使用情况。
    """
    global conversation_history, main_model_tokens, tool_checker_tokens, code_editor_tokens, code_execution_tokens, file_contents, code_editor_files
    conversation_history = []  # 重置对话历史
    main_model_tokens = {'input': 0, 'output': 0}  # 重置主模型token
    tool_checker_tokens = {'input': 0, 'output': 0}  # 重置工具检查器token
    code_editor_tokens = {'input': 0, 'output': 0}  # 重置代码编辑器token
    code_execution_tokens = {'input': 0, 'output': 0}  # 重置代码执行token
    file_contents = {}  # 重置文件内容
    code_editor_files = set()  # 重置代码编辑器文件集合
    reset_code_editor_memory()  # 重置代码编辑器记忆
    console.print(Panel("Conversation history, token counts, file contents, code editor memory, and code editor files have been reset.", title="Reset", style="bold green"))  # 显示重置信息
    display_token_usage()  # 显示token使用情况

def display_token_usage():
    """
    显示token使用情况的函数。

    调用的外部函数:
    - Table(): 创建一个表格对象，用于格式化显示数据。
    - Panel(): 创建一个格式化的面板，用于显示信息。
    - console.print(): 在控制台打印信息。
    """
    from rich.table import Table
    from rich.panel import Panel
    from rich.box import ROUNDED

    table = Table(box=ROUNDED)  # 创建表格
    table.add_column("Model", style="cyan")  # 添加模型列
    table.add_column("Input", style="magenta")  # 添加输入列
    table.add_column("Output", style="magenta")  # 添加输出列
    table.add_column("Cache Write", style="blue")  # 添加缓存写入列
    table.add_column("Cache Read", style="blue")  # 添加缓存读取列
    table.add_column("Total", style="green")  # 添加总计列
    table.add_column(f"% of Context ({MAX_CONTEXT_TOKENS:,})", style="yellow")  # 添加上下文百分比列
    table.add_column("Cost ($)", style="red")  # 添加成本列

    model_costs = {
        "Main Model": {"input": 2.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30, "has_context": True},
        "Tool Checker": {"input": 2.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30, "has_context": False},
        "Code Editor": {"input": 2.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30, "has_context": True},
        "Code Execution": {"input": 2.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30, "has_context": False}
    }

    total_input = 0  # 初始化总输入
    total_output = 0  # 初始化总输出
    total_cache_write = 0  # 初始化总缓存写入
    total_cache_read = 0  # 初始化总缓存读取
    total_cost = 0  # 初始化总成本
    total_context_tokens = 0  # 初始化总上下文token

    for model, tokens in [("Main Model", main_model_tokens),
                          ("Tool Checker", tool_checker_tokens),
                          ("Code Editor", code_editor_tokens),
                          ("Code Execution", code_execution_tokens)]:
        input_tokens = tokens['input']  # 获取输入token
        output_tokens = tokens['output']  # 获取输出token
        cache_write_tokens = tokens.get('cache_creation', 0)  # 获取缓存写入token
        cache_read_tokens = tokens.get('cache_read', 0)  # 获取缓存读取token
        total_tokens = input_tokens + output_tokens + cache_write_tokens + cache_read_tokens  # 计算总token

        total_input += input_tokens  # 累加总输入
        total_output += output_tokens  # 累加总输出
        total_cache_write += cache_write_tokens  # 累加总缓存写入
        total_cache_read += cache_read_tokens  # 累加总缓存读取

        input_cost = (input_tokens / 1_000_000) * model_costs[model]["input"]  # 计算输入成本
        output_cost = (output_tokens / 1_000_000) * model_costs[model]["output"]  # 计算输出成本
        cache_write_cost = (cache_write_tokens / 1_000_000) * model_costs[model]["cache_write"]  # 计算缓存写入成本
        cache_read_cost = (cache_read_tokens / 1_000_000) * model_costs[model]["cache_read"]  # 计算缓存读取成本
        model_cost = input_cost + output_cost + cache_write_cost + cache_read_cost  # 计算模型总成本
        total_cost += model_cost  # 累加总成本

        if model_costs[model]["has_context"]:
            total_context_tokens += total_tokens  # 累加上下文token
            percentage = (total_tokens / MAX_CONTEXT_TOKENS) * 100  # 计算上下文百分比
        else:
            percentage = 0  # 没有上下文的百分比为0

        table.add_row(
            model,
            f"{input_tokens:,}",  # 添加输入token数
            f"{output_tokens:,}",  # 添加输出token数
            f"{cache_write_tokens:,}",  # 添加缓存写入token数
            f"{cache_read_tokens:,}",  # 添加缓存读取token数
            f"{total_tokens:,}",  # 添加总token数
            f"{percentage:.2f}%" if model_costs[model]["has_context"] else "Doesn't save context",  # 添加上下文百分比
            f"${model_cost:.3f}"  # 添加成本
        )

    grand_total = total_input + total_output + total_cache_write + total_cache_read  # 计算总计
    total_percentage = (total_context_tokens / MAX_CONTEXT_TOKENS) * 100  # 计算总上下文百分比

    table.add_row(
        "Total",
        f"{total_input:,}",  # 添加总输入
        f"{total_output:,}",  # 添加总输出
        f"{total_cache_write:,}",  # 添加总缓存写入
        f"{total_cache_read:,}",  # 添加总缓存读取
        f"{grand_total:,}",  # 添加总计
        f"{total_percentage:.2f}%",  # 添加总上下文百分比
        f"${total_cost:.3f}",  # 添加总成本
        style="bold"  # 设置样式为粗体
    )

    console.print(table)  # 打印表格

async def main():
    """
    主函数，控制整体流程。

    调用的外部函数:
    - console.print(): 在控制台打印信息。
    - Panel(): 创建一个格式化的面板，用于显示信息。
    - get_user_input(): 异步获取用户输入。
    - reset_conversation(): 重置对话历史和相关状态。
    - get_format_choice(): 异步获取保存格式选择。
    - save_chat(): 保存聊天记录。
    - load_chat(): 加载聊天记录。
    - chat_with_claude(): 与Claude进行对话。
    """
    global automode, conversation_history
    console.print(Panel("Welcome to the Claude-3-Sonnet Engineer Chat with Multi-Agent and Image Support!", title="Welcome", style="bold green"))  # 欢迎信息
    console.print("Type 'exit' to end the conversation.")  # 退出提示
    console.print("Type 'image' to include an image in your message.")  # 图像提示
    console.print("Type 'automode [number]' to enter Autonomous mode with a specific number of iterations.")  # 自动模式提示
    console.print("Type 'reset' to clear the conversation history.")  # 重置提示
    console.print("Type 'save chat' to save the conversation (you'll be prompted to choose between Markdown and JSON formats).")  # 保存聊天提示
    console.print("Type 'load' to load a previously saved JSON chat file.")  # 加载聊天提示
    console.print("While in automode, press Ctrl+C at any time to exit the automode to return to regular chat.")  # 自动模式退出提示

    while True:
        user_input = await get_user_input()  # 获取用户输入

        if user_input.lower() == 'exit':
            console.print(Panel("Thank you for chatting. Goodbye!", title_align="left", title="Goodbye", style="bold green"))  # 退出信息
            break

        if user_input.lower() == 'reset':
            reset_conversation()  # 重置对话，重置所有对话参数
            continue

        if user_input.lower() == 'save chat':
            format_choice = await get_format_choice()  # 获取保存格式，Markdown或JSON
            if format_choice is None:
                console.print(Panel("Chat save cancelled.", title="Save Cancelled", style="yellow"))  # 保存取消信息
                continue
            filename = save_chat(format=format_choice)  # 保存聊天记录
            console.print(Panel(f"Chat saved to {filename}", title="Chat Saved", style="bold green"))  # 保存成功信息
            continue

        if user_input.lower() == 'load':
            load_path = (await get_user_input("Drag and drop your JSON file here, then press enter: ")).strip().replace("'", "")  # 获取加载路径

            if os.path.isfile(load_path):
                if load_chat(load_path):  # 加载聊天记录
                    console.print(Panel(f"Chat loaded from {load_path}", title="Chat Loaded", style="bold green"))  # 加载成功信息
                else:
                    console.print(Panel("Failed to load chat. Please check the file and try again.", title="Load Error", style="bold red"))  # 加载失败信息
            else:
                console.print(Panel("Invalid file path. Please try again.", title="Error", style="bold red"))  # 文件路径无效信息
            continue

        if user_input.lower() == 'image':
            image_path = (await get_user_input("Drag and drop your image here, then press enter: ")).strip().replace("'", "")  # 获取图像路径

            if os.path.isfile(image_path):
                user_input = await get_user_input("You (prompt for image): ")  # 获取用户输入
                response, _ = await chat_with_claude(user_input, image_path)  # 处理图像
            else:
                console.print(Panel("Invalid image path. Please try again.", title="Error", style="bold red"))  # 图像路径无效信息
                continue
        elif user_input.lower().startswith('automode'):
            try:
                parts = user_input.split()  # 分割用户输入
                if len(parts) > 1 and parts[1].isdigit():
                    max_iterations = int(parts[1])  # 获取最大迭代次数
                else:
                    max_iterations = MAX_CONTINUATION_ITERATIONS  # 默认最大迭代次数

                automode = True  # 设置自动模式为真
                console.print(Panel(f"Entering automode with {max_iterations} iterations. Please provide the goal of the automode.", title_align="left", title="Automode", style="bold yellow"))  # 进入自动模式信息
                console.print(Panel("Press Ctrl+C at any time to exit the automode loop.", style="bold yellow"))  # 自动模式退出提示
                user_input = await get_user_input()  # 获取用户输入

                iteration_count = 0  # 初始化迭代计数
                try:
                    while automode and iteration_count < max_iterations:
                        response, exit_continuation = await chat_with_claude(user_input, current_iteration=iteration_count+1, max_iterations=max_iterations)  # 进行对话

                        if exit_continuation or CONTINUATION_EXIT_PHRASE in response:
                            console.print(Panel("Automode completed.", title_align="left", title="Automode", style="green"))  # 自动模式完成信息
                            automode = False  # 设置自动模式为假
                        else:
                            console.print(Panel(f"Continuation iteration {iteration_count + 1} completed. Press Ctrl+C to exit automode. ", title_align="left", title="Automode", style="yellow"))  # 继续迭代信息
                            user_input = "Continue with the next step. Or STOP by saying 'AUTOMODE_COMPLETE' if you think you've achieved the results established in the original request."  # 提示继续
                        iteration_count += 1  # 增加迭代计数

                        if iteration_count >= max_iterations:
                            console.print(Panel("Max iterations reached. Exiting automode.", title_align="left", title="Automode", style="bold red"))  # 达到最大迭代次数信息
                            automode = False  # 设置自动模式为假
                except KeyboardInterrupt:
                    console.print(Panel("\nAutomode interrupted by user. Exiting automode.", title_align="left", title="Automode", style="bold red"))  # 用户中断自动模式信息
                    automode = False  # 设置自动模式为假
                    if conversation_history and conversation_history[-1]["role"] == "user":
                        conversation_history.append({"role": "assistant", "content": "Automode interrupted. How can I assist you further?"})  # 添加助手响应
            except KeyboardInterrupt:
                console.print(Panel("\nAutomode interrupted by user. Exiting automode.", title_align="left", title="Automode", style="bold red"))  # 用户中断自动模式信息
                automode = False  # 设置自动模式为假
                if conversation_history and conversation_history[-1]["role"] == "user":
                    conversation_history.append({"role": "assistant", "content": "Automode interrupted. How can I assist you further?"})  # 添加助手响应

            console.print(Panel("Exited automode. Returning to regular chat.", style="green"))  # 退出自动模式信息
        else:
            response, _ = await chat_with_claude(user_input)  # 进行对话

if __name__ == "__main__":
    asyncio.run(main())  # 运行主函数
