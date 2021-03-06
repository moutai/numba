"""
Implementation of tuple objects
"""

from llvmlite import ir
import llvmlite.llvmpy.core as lc

from .imputils import (lower_builtin, lower_getattr_generic, lower_cast,
                       iternext_impl, impl_ret_borrowed, impl_ret_untracked)
from .. import typing, types, cgutils


@lower_builtin(types.NamedTupleClass, types.VarArg(types.Any))
def namedtuple_constructor(context, builder, sig, args):
    # A namedtuple has the same representation as a regular tuple
    res = context.make_tuple(builder, sig.return_type, args)
    # The tuple's contents are borrowed
    return impl_ret_borrowed(context, builder, sig.return_type, res)

@lower_builtin(len, types.BaseTuple)
def tuple_len(context, builder, sig, args):
    tupty, = sig.args
    retty = sig.return_type
    res = context.get_constant(retty, len(tupty.types))
    return impl_ret_untracked(context, builder, sig.return_type, res)

@lower_builtin(bool, types.BaseTuple)
def tuple_bool(context, builder, sig, args):
    tupty, = sig.args
    if len(tupty):
        return cgutils.true_bit
    else:
        return cgutils.false_bit

@lower_builtin('+', types.BaseTuple, types.BaseTuple)
def tuple_add(context, builder, sig, args):
    left, right = [cgutils.unpack_tuple(builder, x) for x in args]
    return context.make_tuple(builder, sig.return_type, left + right)

def tuple_cmp_ordered(context, builder, op, sig, args):
    tu, tv = sig.args
    u, v = args
    res = cgutils.alloca_once_value(builder, cgutils.true_bit)
    bbend = builder.append_basic_block("cmp_end")
    for i, (ta, tb) in enumerate(zip(tu.types, tv.types)):
        a = builder.extract_value(u, i)
        b = builder.extract_value(v, i)
        not_equal = context.generic_compare(builder, '!=', (ta, tb), (a, b))
        with builder.if_then(not_equal):
            pred = context.generic_compare(builder, op, (ta, tb), (a, b))
            builder.store(pred, res)
            builder.branch(bbend)
    # Everything matched equal => compare lengths
    len_compare = eval("%d %s %d" % (len(tu.types), op, len(tv.types)))
    pred = context.get_constant(types.boolean, len_compare)
    builder.store(pred, res)
    builder.branch(bbend)
    builder.position_at_end(bbend)
    return builder.load(res)

@lower_builtin('==', types.BaseTuple, types.BaseTuple)
def tuple_eq(context, builder, sig, args):
    tu, tv = sig.args
    u, v = args
    if len(tu.types) != len(tv.types):
        res = context.get_constant(types.boolean, False)
        return impl_ret_untracked(context, builder, sig.return_type, res)
    res = context.get_constant(types.boolean, True)
    for i, (ta, tb) in enumerate(zip(tu.types, tv.types)):
        a = builder.extract_value(u, i)
        b = builder.extract_value(v, i)
        pred = context.generic_compare(builder, "==", (ta, tb), (a, b))
        res = builder.and_(res, pred)
    return impl_ret_untracked(context, builder, sig.return_type, res)

@lower_builtin('!=', types.BaseTuple, types.BaseTuple)
def tuple_ne(context, builder, sig, args):
    res = builder.not_(tuple_eq(context, builder, sig, args))
    return impl_ret_untracked(context, builder, sig.return_type, res)

@lower_builtin('<', types.BaseTuple, types.BaseTuple)
def tuple_lt(context, builder, sig, args):
    res = tuple_cmp_ordered(context, builder, '<', sig, args)
    return impl_ret_untracked(context, builder, sig.return_type, res)

@lower_builtin('<=', types.BaseTuple, types.BaseTuple)
def tuple_le(context, builder, sig, args):
    res = tuple_cmp_ordered(context, builder, '<=', sig, args)
    return impl_ret_untracked(context, builder, sig.return_type, res)

@lower_builtin('>', types.BaseTuple, types.BaseTuple)
def tuple_gt(context, builder, sig, args):
    res = tuple_cmp_ordered(context, builder, '>', sig, args)
    return impl_ret_untracked(context, builder, sig.return_type, res)

@lower_builtin('>=', types.BaseTuple, types.BaseTuple)
def tuple_ge(context, builder, sig, args):
    res = tuple_cmp_ordered(context, builder, '>=', sig, args)
    return impl_ret_untracked(context, builder, sig.return_type, res)


@lower_getattr_generic(types.BaseNamedTuple)
def namedtuple_getattr(context, builder, typ, value, attr):
    """
    Fetch a namedtuple's field.
    """
    index = typ.fields.index(attr)
    res = builder.extract_value(value, index)
    return impl_ret_borrowed(context, builder, typ[index], res)


#------------------------------------------------------------------------------
# Tuple iterators

def make_unituple_iter(tupiter):
    """
    Return the Structure representation of the given *tupiter* (an
    instance of types.UniTupleIter).
    """
    return cgutils.create_struct_proxy(tupiter)


@lower_builtin('getiter', types.UniTuple)
@lower_builtin('getiter', types.NamedUniTuple)
def getiter_unituple(context, builder, sig, args):
    [tupty] = sig.args
    [tup] = args

    tupitercls = make_unituple_iter(types.UniTupleIter(tupty))
    iterval = tupitercls(context, builder)

    index0 = context.get_constant(types.intp, 0)
    indexptr = cgutils.alloca_once(builder, index0.type)
    builder.store(index0, indexptr)

    iterval.index = indexptr
    iterval.tuple = tup

    res = iterval._getvalue()
    return impl_ret_borrowed(context, builder, sig.return_type, res)


@lower_builtin('iternext', types.UniTupleIter)
@iternext_impl
def iternext_unituple(context, builder, sig, args, result):
    [tupiterty] = sig.args
    [tupiter] = args

    tupitercls = make_unituple_iter(tupiterty)
    iterval = tupitercls(context, builder, value=tupiter)
    tup = iterval.tuple
    idxptr = iterval.index
    idx = builder.load(idxptr)
    count = context.get_constant(types.intp, tupiterty.unituple.count)

    is_valid = builder.icmp(lc.ICMP_SLT, idx, count)
    result.set_valid(is_valid)

    with builder.if_then(is_valid):
        getitem_sig = typing.signature(tupiterty.unituple.dtype,
                                       tupiterty.unituple,
                                       types.intp)
        getitem_out = getitem_unituple(context, builder, getitem_sig,
                                       [tup, idx])
        result.yield_(getitem_out)
        nidx = builder.add(idx, context.get_constant(types.intp, 1))
        builder.store(nidx, iterval.index)


@lower_builtin('getitem', types.UniTuple, types.intp)
@lower_builtin('getitem', types.NamedUniTuple, types.intp)
def getitem_unituple(context, builder, sig, args):
    tupty, _ = sig.args
    tup, idx = args

    bbelse = builder.append_basic_block("switch.else")
    bbend = builder.append_basic_block("switch.end")
    switch = builder.switch(idx, bbelse, n=tupty.count)

    with builder.goto_block(bbelse):
        context.call_conv.return_user_exc(builder, IndexError,
                                          ("tuple index out of range",))

    lrtty = context.get_value_type(tupty.dtype)
    with builder.goto_block(bbend):
        phinode = builder.phi(lrtty)

    for i in range(tupty.count):
        ki = context.get_constant(types.intp, i)
        bbi = builder.append_basic_block("switch.%d" % i)
        switch.add_case(ki, bbi)
        with builder.goto_block(bbi):
            value = builder.extract_value(tup, i)
            builder.branch(bbend)
            phinode.add_incoming(value, bbi)

    builder.position_at_end(bbend)
    res = phinode
    assert sig.return_type == tupty.dtype
    return impl_ret_borrowed(context, builder, sig.return_type, res)


@lower_builtin('static_getitem', types.BaseTuple, types.Const)
def static_getitem_tuple(context, builder, sig, args):
    tupty, _ = sig.args
    tup, idx = args
    if isinstance(idx, int):
        res = builder.extract_value(tup, idx)
    elif isinstance(idx, slice):
        items = cgutils.unpack_tuple(builder, tup)[idx]
        res = context.make_tuple(builder, sig.return_type, items)
    else:
        raise NotImplementedError("unexpected index %r for %s"
                                  % (idx, sig.args[0]))
    return impl_ret_borrowed(context, builder, sig.return_type, res)


#------------------------------------------------------------------------------
# Implicit conversion

@lower_cast(types.BaseTuple, types.BaseTuple)
def tuple_to_tuple(context, builder, fromty, toty, val):
    if (isinstance(fromty, types.BaseNamedTuple)
        or isinstance(toty, types.BaseNamedTuple)):
        # Disallowed by typing layer
        raise NotImplementedError

    if len(fromty) != len(toty):
        # Disallowed by typing layer
        raise NotImplementedError

    olditems = cgutils.unpack_tuple(builder, val, len(fromty))
    items = [context.cast(builder, v, f, t)
             for v, f, t in zip(olditems, fromty, toty)]
    return context.make_tuple(builder, toty, items)
