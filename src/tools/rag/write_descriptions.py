import os
from pathlib import Path
from langchain.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv, find_dotenv
import chromadb
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from src.utilities.util_functions import join_paths, read_coderrules
from src.utilities.start_work_functions import CoderIgnore, file_folder_ignored
from src.utilities.llms import init_llms_mini
from src.tools.rag.code_splitter import split_code
from src.utilities.print_formatters import print_formatted


load_dotenv(find_dotenv())
work_dir = os.getenv("WORK_DIR")


def is_code_file(file_path):
    # List of common code file extensions
    code_extensions = {
        '.js', '.jsx', '.ts', '.tsx', '.vue', '.py', '.rb', '.php', '.java', '.c', '.cpp', '.cs', '.go', '.swift',
        '.kt', '.rs', '.htm','.html', '.css', '.scss', '.sass', '.less', '.prompt',
    }
    return file_path.suffix.lower() in code_extensions


# read file content. place name of file in the top
def get_content(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    content = file_path.name + '\n\n' + content
    return content

def collect_file_pathes(subfolders, work_dir):
    """
    Collect and return a list of allowed code files from the given subfolders
    under the work_dir according to is_code_file criteria and .coderignore patterns.
    """
    allowed_files = []
    for folder in subfolders:
        for root, _, files in os.walk(work_dir + folder):
            for file in files:
                file_path = Path(root) / file
                if not is_code_file(file_path):
                    continue
                relative_path_str = file_path.relative_to(work_dir).as_posix()
                if file_folder_ignored(relative_path_str):
                    continue
                allowed_files.append(file_path)
    return allowed_files


def write_file_descriptions(subfolders_with_files=['/']):
    all_files = collect_file_pathes(subfolders_with_files, work_dir)

    coderrules = read_coderrules()

    prompt = ChatPromptTemplate.from_template(
f"""First, get known with info about project (may be useful, may be not):

'''
{coderrules}
'''

Describe the code in 4 sentences or less, focusing only on important information from integration point of view.
Write what file is responsible for.

Go straight to the thing in description, without starting sentence.

'''
{{code}}
'''
"""
    )
    llms = init_llms_mini(tools=[], run_name='File Describer')
    llm = llms[0].with_fallbacks(llms[1:])
    chain = prompt | llm | StrOutputParser()

    description_folder = join_paths(work_dir, '.clean_coder/files_and_folders_descriptions')
    Path(description_folder).mkdir(parents=True, exist_ok=True)
    # iterate over all files, take 8 files at once and descrive files in batch
    batch_size = 8
    for i in range(0, len(all_files), batch_size):
        files_iteration = all_files[i:i + batch_size]
        descriptions = chain.batch([get_content(file_path) for file_path in files_iteration])
        print(descriptions)

        for file_path, description in zip(files_iteration, descriptions):
            file_name = file_path.relative_to(work_dir).as_posix().replace('/', '=')
            output_path = join_paths(description_folder, f"{file_name}.txt")

            with open(output_path, 'w', encoding='utf-8') as out_file:
                out_file.write(description)



def write_file_chunks_descriptions(subfolders_with_files=['/']):
    """Writes descriptions of whole file chunks in codebase. Gets list of whole files to describe, divides files
    into chunks and describes each chunk separately."""
    all_files = collect_file_pathes(subfolders_with_files, work_dir)
    coderrules = read_coderrules()

    grandparent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    with open(f"{grandparent_dir}/prompts/describe_file_chunks.prompt", "r") as f:
        chunks_describe_template = f.read()

    prompt = ChatPromptTemplate.from_template(chunks_describe_template)
    llms = init_llms_mini(tools=[], run_name='File Describer')
    llm = llms[0]
    chain = prompt | llm | StrOutputParser()

    description_folder = join_paths(work_dir, '.clean_coder/files_and_folders_descriptions')
    Path(description_folder).mkdir(parents=True, exist_ok=True)
    # iterate chunks inside of the file
    for file_path in all_files:
        file_content = get_content(file_path)
        # get file extenstion
        extension = file_path.suffix.lstrip('.')
        file_chunks = split_code(file_content, extension)
        # do not describe chunk of 1-chunk files
        if len(file_chunks) <= 1:
            continue
        descriptions = chain.batch([{'coderrules': coderrules, 'file_code': file_content, 'chunk_code': chunk} for chunk in file_chunks])
        print(descriptions)

        for nr, description in enumerate(descriptions):
            file_name = f"{file_path.relative_to(work_dir).as_posix().replace('/', '=')}_chunk{nr}"
            output_path = join_paths(description_folder, f"{file_name}.txt")

            with open(output_path, 'w', encoding='utf-8') as out_file:
                out_file.write(description)


def upload_descriptions_to_vdb():
    """Uploads descriptions, created by write_file_chunks_descriptions, into vector database."""
    print_formatted("Uploading file descriptions to vector storage...", color='magenta')
    chroma_client = chromadb.PersistentClient(path=join_paths(work_dir, '.clean_coder/chroma_base'))
    collection_name = f"clean_coder_{Path(work_dir).name}_file_descriptions"

    collection = chroma_client.get_or_create_collection(
        name=collection_name
    )

    # read files and upload to base
    description_folder = join_paths(work_dir, '.clean_coder/files_and_folders_descriptions')
    for root, _, files in os.walk(description_folder):
        for file in files:
            file_path = Path(root) / file
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
            collection.upsert(
                documents=[
                    content
                ],
                ids=[file_path.name.replace('=', '/').removesuffix(".txt")],
            )


if __name__ == '__main__':
    #provide optionally which subfolders needs to be checked, if you don't want to describe all project folder
    write_file_descriptions(subfolders_with_files=['/'])
    write_file_chunks_descriptions()

    upload_descriptions_to_vdb()