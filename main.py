"""
通用作家风格仿写 Skill - CLI 入口

使用方法:
    python main.py create --name "鲁迅" --source ./luxun_files/
    python main.py write --author "鲁迅" --topic "故乡" --tone philosophical
    python main.py list
    python main.py info --author "liu_liangcheng"
"""

import sys
import argparse
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from skills.author_style import AuthorStyleSkill, create_author, list_authors, delete_author, get_author_info
from skills.author_style.safety import safe_filename


def cmd_create(args):
    """创建作家 Skill"""
    print(f"正在创建作家: {args.name}")
    if args.source:
        print(f"源文件路径: {args.source}")

    author_dir = create_author(
        name=args.name,
        source_path=args.source,
        analyze=not args.no_analyze,
        build_index=not args.no_index,
    )
    print(f"\n创建完成: {author_dir}")


def cmd_write(args):
    """生成文章"""
    print("=" * 60)
    print(f"作家: {args.author} | 主题: {args.topic}")
    print(f"风格: {args.tone} | 长度: {args.length} | RAG: {'启用' if not args.no_retrieval else '禁用'}")
    print("=" * 60)
    print("\n正在生成，请稍候...\n")

    try:
        skill = AuthorStyleSkill(
            author_name=args.author,
            model=args.model,
            temperature=args.temperature,
        )
        article = skill.write(
            topic=args.topic,
            tone=args.tone,
            length=args.length,
            include_retrieval=not args.no_retrieval,
        )

        # 保存到作家目录
        output_dir = Path("authors") / args.author / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = output_dir / safe_filename(args.topic, default="article", suffix=".txt")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(article)
        print(f"文章已保存到: {filename}")

        print(article)
        print("\n" + "=" * 60)
        print("生成完成")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\n已取消")
        sys.exit(0)
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)


def cmd_list(args):
    """列出所有作家"""
    authors = list_authors()

    if not authors:
        print("暂无作家，请使用 create 命令创建")
        return

    print(f"共 {len(authors)} 位作家:\n")
    print(f"{'名称':<20} {'风格指南':<10} {'向量库':<10} {'txt':<6} {'epub':<6}")
    print("-" * 60)

    for author in authors:
        guide_icon = "✓" if author["has_style_guide"] else "✗"
        index_icon = "✓" if author["has_vector_store"] else "✗"
        print(f"{author['name']:<20} {guide_icon:<10} {index_icon:<10} {author['txt_files']:<6} {author['epub_files']:<6}")


def cmd_info(args):
    """显示作家详细信息"""
    info = get_author_info(args.author)
    if not info:
        print(f"作家 '{args.author}' 不存在")
        return

    print(f"作家: {info['name']}")
    print(f"目录: {info['dir']}")
    print(f"风格指南: {'✓' if info['has_style_guide'] else '✗'}")
    print(f"Few-shot: {'✓' if info['has_few_shot'] else '✗'}")
    print(f"向量索引: {'✓' if info['has_vector_store'] else '✗'}")

    if info["txt_files"]:
        print(f"\ntxt 文件 ({len(info['txt_files'])}):")
        for f in info["txt_files"]:
            print(f"  - {f}")

    if info["epub_files"]:
        print(f"\nepub 文件 ({len(info['epub_files'])}):")
        for f in info["epub_files"]:
            print(f"  - {f}")


def cmd_delete(args):
    """删除作家"""
    delete_author(args.author, confirm=True)


def main():
    parser = argparse.ArgumentParser(
        description="通用作家风格仿写 Skill - 上传作品，自动分析风格，仿写创作",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py create --name "鲁迅" --source ./luxun_files/
  python main.py create --name "莫言" --source ./moyan.epub
  python main.py write --author "鲁迅" --topic "故乡" --tone sharp
  python main.py write --author "liu_liangcheng" --topic "故乡的狗"
  python main.py list
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # create 子命令
    create_parser = subparsers.add_parser("create", help="创建作家 Skill")
    create_parser.add_argument("--name", "-n", type=str, required=True, help="作家名称（用作目录名）")
    create_parser.add_argument("--source", "-s", type=str, help="源文件路径（目录或单个文件）")
    create_parser.add_argument("--no-analyze", action="store_true", help="跳过风格分析")
    create_parser.add_argument("--no-index", action="store_true", help="跳过向量索引构建")

    # write 子命令
    write_parser = subparsers.add_parser("write", help="生成文章")
    write_parser.add_argument("--author", "-a", type=str, required=True, help="作家名称")
    write_parser.add_argument("--topic", "-t", type=str, required=True, help="写作主题")
    write_parser.add_argument("--tone", type=str, default="default", help="风格选项")
    write_parser.add_argument("--length", type=str, default="medium", choices=["short", "medium", "long"], help="长度选项")
    write_parser.add_argument("--model", type=str, help="模型名称（覆盖作家配置）")
    write_parser.add_argument("--temperature", type=float, help="生成温度（覆盖作家配置）")
    write_parser.add_argument("--no-retrieval", action="store_true", help="禁用 RAG 检索")

    # list 子命令
    subparsers.add_parser("list", help="列出所有作家")

    # info 子命令
    info_parser = subparsers.add_parser("info", help="显示作家详细信息")
    info_parser.add_argument("--author", "-a", type=str, required=True, help="作家名称")

    # delete 子命令
    delete_parser = subparsers.add_parser("delete", help="删除作家")
    delete_parser.add_argument("--author", "-a", type=str, required=True, help="作家名称")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "create": cmd_create,
        "write": cmd_write,
        "list": cmd_list,
        "info": cmd_info,
        "delete": cmd_delete,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
