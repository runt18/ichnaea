from datetime import date, datetime

import colander


class DateFromString(colander.Date):
    """
    A DateFromString will return a date object
    from either a date object or a string.
    """

    def deserialize(self, schema, cstruct):
        if type(cstruct) == date:
            return cstruct
        return super(DateFromString, self).deserialize(
            schema, cstruct)  # pragma: no cover


class DateTimeFromString(colander.DateTime):
    """
    A DateTimeFromString will return a datetime object
    from either a datetime object or a string.
    """

    def deserialize(self, schema, cstruct):
        if type(cstruct) == datetime:
            return cstruct
        return super(DateTimeFromString, self).deserialize(schema, cstruct)


class ValidatorNode(colander.SchemaNode):
    """
    A ValidatorNode is a schema node which defines validator as a callable
    method, so all subclasses can rely on calling it via super().
    """

    def validator(self, node, cstruct):
        pass


class DefaultNode(ValidatorNode):
    """
    A DefaultNode will use its ``missing`` value
    if it fails to validate during deserialization.
    """

    def deserialize(self, cstruct):
        try:
            return super(DefaultNode, self).deserialize(cstruct)
        except colander.Invalid:
            if self.missing is colander.required:
                raise
            return self.missing
