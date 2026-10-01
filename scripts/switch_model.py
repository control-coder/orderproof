"""一键切换当前模型供应商；只改写 .env 中的供应商一行，不读取或输出密钥。"""
import argparse
import json

from datalab.roles.model_config import CHOICES, load_model_config, switch_provider


def main():
    parser = argparse.ArgumentParser(description='切换 OrderProof 三角色使用的模型；不带参数时显示当前状态')
    parser.add_argument('provider', nargs='?', choices=CHOICES)
    args = parser.parse_args()
    if args.provider:
        switch_provider(args.provider)
    config = load_model_config()
    print(json.dumps({'active': config.active, 'providers': [item.public() for item in config.profiles.values()]},
                     ensure_ascii=False, indent=2))
    print('新建任务即生效，已创建任务仍使用创建时记录的模型；worker 无需重启。')


if __name__ == '__main__':
    main()
