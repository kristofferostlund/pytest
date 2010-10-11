"""
test collection nodes, forming a tree, Items are leafs.
"""
import py

__all__ = ['Collector', 'Item', 'File', 'Directory']

def configproperty(name):
    def fget(self):
        #print "retrieving %r property from %s" %(name, self.fspath)
        return self.config._getcollectclass(name, self.fspath)
    return property(fget)

class HookProxy:
    def __init__(self, node):
        self.node = node
    def __getattr__(self, name):
        if name[0] == "_":
            raise AttributeError(name)
        hookmethod = getattr(self.node.config.hook, name)
        def call_matching_hooks(**kwargs):
            plugins = self.node.config._getmatchingplugins(self.node.fspath)
            return hookmethod.pcall(plugins, **kwargs)
        return call_matching_hooks

class Node(object):
    """ base class for all Nodes in the collection tree.
    Collector subclasses have children, Items are terminal nodes."""

    def __init__(self, name, parent=None, config=None, collection=None):
        #: a unique name with the scope of the parent
        self.name = name

        #: the parent collector node.
        self.parent = parent
        
        self.config = config or parent.config
        #: the collection this node is part of.
        self.collection = collection or getattr(parent, 'collection', None)
        
        #: the file where this item is contained/collected from.
        self.fspath = getattr(parent, 'fspath', None)
        self.ihook = HookProxy(self)
        self.keywords = self.readkeywords()

    def __repr__(self):
        if getattr(self.config.option, 'debug', False):
            return "<%s %r %0x>" %(self.__class__.__name__,
                getattr(self, 'name', None), id(self))
        else:
            return "<%s %r>" %(self.__class__.__name__,
                getattr(self, 'name', None))

    # methods for ordering nodes

    def __eq__(self, other):
        if not isinstance(other, Node):
            return False
        return self.__class__ == other.__class__ and \
               self.name == other.name and self.parent == other.parent 

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.name, self.parent))

    def setup(self):
        pass

    def teardown(self):
        pass

    def _memoizedcall(self, attrname, function):
        exattrname = "_ex_" + attrname
        failure = getattr(self, exattrname, None)
        if failure is not None:
            py.builtin._reraise(failure[0], failure[1], failure[2])
        if hasattr(self, attrname):
            return getattr(self, attrname)
        try:
            res = function()
        except py.builtin._sysex:
            raise
        except:
            failure = py.std.sys.exc_info()
            setattr(self, exattrname, failure)
            raise
        setattr(self, attrname, res)
        return res

    def listchain(self):
        """ return list of all parent collectors up to self,
            starting from root of collection tree. """
        l = [self]
        while 1:
            x = l[0]
            if x.parent is not None: # and x.parent.parent is not None:
                l.insert(0, x.parent)
            else:
                return l

    def listnames(self):
        return [x.name for x in self.listchain()]

    def getparent(self, cls):
        current = self
        while current and not isinstance(current, cls):
            current = current.parent
        return current

    def readkeywords(self):
        return dict([(x, True) for x in self._keywords()])

    def _keywords(self):
        return [self.name]

    def _prunetraceback(self, traceback):
        return traceback

    def _repr_failure_py(self, excinfo, style=None):
        if self.config.option.fulltrace:
            style="long"
        else:
            excinfo.traceback = self._prunetraceback(excinfo.traceback)
        # XXX should excinfo.getrepr record all data and toterminal()
        # process it?
        if style is None:
            if self.config.option.tbstyle == "short":
                style = "short"
            else:
                style = "long"
        return excinfo.getrepr(funcargs=True,
                               showlocals=self.config.option.showlocals,
                               style=style)

    repr_failure = _repr_failure_py

class Collector(Node):
    """ Collector instances create children through collect()
        and thus iteratively build a tree.
    """
    Directory = configproperty('Directory')
    Module = configproperty('Module')
    class CollectError(Exception):
        """ an error during collection, contains a custom message. """

    def collect(self):
        """ returns a list of children (items and collectors)
            for this collection node.
        """
        raise NotImplementedError("abstract")

    def collect_by_name(self, name):
        """ return a child matching the given name, else None. """
        for colitem in self._memocollect():
            if colitem.name == name:
                return colitem

    def repr_failure(self, excinfo):
        """ represent a failure. """
        if excinfo.errisinstance(self.CollectError):
            exc = excinfo.value
            return str(exc.args[0])
        return self._repr_failure_py(excinfo, style="short")

    def _memocollect(self):
        """ internal helper method to cache results of calling collect(). """
        return self._memoizedcall('_collected', self.collect)

    def _prunetraceback(self, traceback):
        if hasattr(self, 'fspath'):
            path = self.fspath
            ntraceback = traceback.cut(path=self.fspath)
            if ntraceback == traceback:
                ntraceback = ntraceback.cut(excludepath=py._pydir)
            traceback = ntraceback.filter()
        return traceback

    # **********************************************************************
    # DEPRECATED METHODS
    # **********************************************************************

    def _deprecated_collect(self):
        # avoid recursion:
        # collect -> _deprecated_collect -> custom run() ->
        # super().run() -> collect
        attrname = '_depcollectentered'
        if hasattr(self, attrname):
            return
        setattr(self, attrname, True)
        method = getattr(self.__class__, 'run', None)
        if method is not None and method != Collector.run:
            warnoldcollect(function=method)
            names = self.run()
            return [x for x in [self.join(name) for name in names] if x]

    def run(self):
        """ DEPRECATED: returns a list of names available from this collector.
            You can return an empty list.  Callers of this method
            must take care to catch exceptions properly.
        """
        return [colitem.name for colitem in self._memocollect()]

    def join(self, name):
        """  DEPRECATED: return a child collector or item for the given name.
             If the return value is None there is no such child.
        """
        return self.collect_by_name(name)

class FSCollector(Collector):
    def __init__(self, fspath, parent=None, config=None, collection=None):
        fspath = py.path.local(fspath)
        super(FSCollector, self).__init__(fspath.basename,
            parent, config, collection)
        self.fspath = fspath

class File(FSCollector):
    """ base class for collecting tests from a file. """

class Directory(FSCollector):
    def recfilter(self, path):
        if path.check(dir=1, dotfile=0):
            return path.basename not in ('CVS', '_darcs', '{arch}')

    def collect(self):
        l = self._deprecated_collect()
        if l is not None:
            return l
        l = []
        for path in self.fspath.listdir(sort=True):
            res = self.consider(path)
            if res is not None:
                if isinstance(res, (list, tuple)):
                    l.extend(res)
                else:
                    l.append(res)
        return l

    def consider(self, path):
        if self.ihook.pytest_ignore_collect(path=path, config=self.config):
           return
        if path.check(file=1):
            res = self.consider_file(path)
        elif path.check(dir=1):
            res = self.consider_dir(path)
        else:
            res = None
        if isinstance(res, list):
            # throw out identical results
            l = []
            for x in res:
                if x not in l:
                    assert x.parent == self, (x.parent, self)
                    assert x.fspath == path, (x.fspath, path)
                    l.append(x)
            res = l
        return res

    def consider_file(self, path):
        return self.ihook.pytest_collect_file(path=path, parent=self)

    def consider_dir(self, path, usefilters=None):
        if usefilters is not None:
            py.log._apiwarn("0.99", "usefilters argument not needed")
        return self.ihook.pytest_collect_directory(path=path, parent=self)

class Item(Node):
    """ a basic test invocation item. Note that for a single function
    there might be multiple test invocation items. Attributes:
    
    """
    def _deprecated_testexecution(self):
        if self.__class__.run != Item.run:
            warnoldtestrun(function=self.run)
        elif self.__class__.execute != Item.execute:
            warnoldtestrun(function=self.execute)
        else:
            return False
        self.run()
        return True

    def run(self):
        """ deprecated, here because subclasses might call it. """
        return self.execute(self.obj)

    def execute(self, obj):
        """ deprecated, here because subclasses might call it. """
        return obj()

    def reportinfo(self):
        return self.fspath, None, ""

def warnoldcollect(function=None):
    py.log._apiwarn("1.0",
        "implement collector.collect() instead of "
        "collector.run() and collector.join()",
        stacklevel=2, function=function)

def warnoldtestrun(function=None):
    py.log._apiwarn("1.0",
        "implement item.runtest() instead of "
        "item.run() and item.execute()",
        stacklevel=2, function=function)
