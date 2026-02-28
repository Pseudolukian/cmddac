from ruamel.yaml.comments import CommentedMap, CommentedSeq

def toc_to_mkdocs_nav(toc):
    nav = []
    
    for title, content in toc.items():
        if isinstance(content, str):
            # просто страница
            nav.append({title: content})
            
        elif isinstance(content, (list, CommentedSeq)):
            # подразделы
            children = []
            for item in content:
                if isinstance(item, str):
                    if ':' in item and not item.strip().startswith('http'):
                        # "Название: путь.md"
                        sub_title, path = [x.strip() for x in item.split(':', 1)]
                        children.append({sub_title: path})
                    else:
                        # просто путь без названия → оставляем как есть
                        children.append(item)
                else:
                    # на всякий случай, если там уже словарь
                    children.append(item)
                    
            nav.append({title: children})
            
        else:
            # редкий случай — просто добавляем как есть
            nav.append({title: content})
            
    return nav