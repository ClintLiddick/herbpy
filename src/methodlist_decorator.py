import types

def CreateMethodListDecorator():
    class MethodListDecorator:
        methods = list()

        def __init__(self, func):
            self._func = func
            self.__class__.methods.append(func)

        def __call__(self, *args):
            self._func(*args)

        @classmethod
        def Bind(cls, instance):
            for method in cls.methods:
                bound_method = types.MethodType(method, instance, type(instance))
                setattr(instance, method.__name__, bound_method)

    return MethodListDecorator
