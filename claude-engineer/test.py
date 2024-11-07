def read_file(path: str) -> str:
    """
    读取指定路径的文件内容并返回。
    参数:
    path (str): 文件的路径。
    返回:
    str: 文件的内容。
    """
    with open(path, 'r', encoding='utf-8') as file:
        return file.read()

def write_file(path: str, content: str) -> None:
    """
    将内容写入指定路径的文件。
    参数:
    path (str): 文件的路径。
    content (str): 要写入文件的内容。
    返回:
    None
    """
    with open(path, 'w', encoding='utf-8') as file:
        file.write(content)
