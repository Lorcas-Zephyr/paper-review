import os

import psycopg2

# 数据库连接信息
host = os.getenv("DB_HOST", "127.0.0.1")
port = int(os.getenv("DB_PORT", "5432"))
databases = [os.getenv("DB_NAME", "postgres")]
user = os.getenv("DB_USER", "postgres")
password = os.getenv("DB_PASSWORD", "")

def inspect_database(db_name):
    try:
        print(f"\n=== 尝试连接数据库: {db_name} ===")
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=db_name,
            user=user,
            password=password
        )
        print(f"连接成功！")

        cur = conn.cursor()

        # 列出所有表
        print(f"\n=== {db_name} 中的所有表 ===")
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        tables = cur.fetchall()

        if not tables:
            print("没有找到表")
        else:
            for table in tables:
                table_name = table[0]
                print(f"\n- 表名: {table_name}")

                # 查看表结构
                print("  表结构:")
                cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table_name}';")
                columns = cur.fetchall()
                for col in columns:
                    print(f"    {col[0]}: {col[1]}")

                # 查看表中的前5条数据
                print("  前5条数据:")
                try:
                    cur.execute(f"SELECT * FROM {table_name} LIMIT 5;")
                    rows = cur.fetchall()

                    if rows:
                        # 打印表头
                        cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}';")
                        column_names = [col[0] for col in cur.fetchall()]
                        print("    " + " | ".join(column_names))
                        print("    " + "-" * 50)

                        # 打印数据
                        for row in rows:
                            # 限制每个字段的长度，避免输出过长
                            row_str = []
                            for item in row:
                                if isinstance(item, str):
                                    row_str.append(item[:50] + "..." if len(item) > 50 else item)
                                else:
                                    row_str.append(str(item))
                            print("    " + " | ".join(row_str))
                    else:
                        print("    表为空")
                except Exception as e:
                    print(f"    无法查询数据: {e}")

        cur.close()
        conn.close()
        print(f"\n{db_name} 连接已关闭")
        return True
    except Exception as e:
        print(f"连接 {db_name} 时出错: {e}")
        return False

try:
    # 尝试连接每个数据库
    success = False
    for db in databases:
        if inspect_database(db):
            success = True

    if not success:
        print("\n所有数据库连接失败，请检查连接信息")

except Exception as e:
    print(f"执行过程中出错: {e}")
