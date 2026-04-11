"""打包入口薄壳。

根目录只保留一个稳定发布入口，具体打包流程由 `tools.build_bundle` 承担。
"""

from tools.build_bundle import main


if __name__ == "__main__":
    main()
