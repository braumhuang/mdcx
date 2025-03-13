# coding=utf-8

class Comma:
    @classmethod
    def join(cls, data: list) -> str:
        strip_list = [string.strip() for string in data]
        return ','.join(strip_list)


if __name__ == '__main__':
    data_list = [' a ', ' b', 'c ']
    print(Comma.join(data_list))
