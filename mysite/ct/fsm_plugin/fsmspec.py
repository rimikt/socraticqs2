from ct.models import FSM

class FSMSpecification(object):
    'convenience class for specifying an FSM graph, loading it'
    def __init__(self, name, title, nodeDict=None, edges=None,
                 pluginNodes=(), attrs=('help', 'path', 'data', 'doLogging'),
                 fsmGroups=(), **kwargs):
        kwargs['name'] = name
        kwargs['title'] = title
        self.fsmData = kwargs
        self.fsmGroups = fsmGroups
        if not nodeDict:
            nodeDict = {}
        if not edges:
            edges = []
        for node in pluginNodes: # expect list of node class objects
            modName = node.__module__.split('.')[-1]
            name = node.__name__
            d = dict(title=node.title, funcName=modName + '.' + name,
                     description=getattr(node, '__doc__', None))
            for attr in attrs: # save node attributes
                if hasattr(node, attr):
                    d[attr] = getattr(node, attr)
            nodeDict[name] = d
            for e in getattr(node, 'edges', ()):
                e = e.copy() # prevent side effects
                e['fromNode'] = name
                edges.append(e)
        self.nodeData = nodeDict
        self.edgeData = edges
    def save_graph(self, *args, **kwargs):
        'load this FSM specification into the database'
        return FSM.save_graph(self.fsmData, self.nodeData, self.edgeData,
                              fsmGroups=self.fsmGroups, *args, **kwargs)
            
class CallerNode(object):
    'base class for node representing a call to a sub-FSM'
    def exceptCancel_edge(self, edge, fsmStack, request, **kwargs):
        'implements default behavior: if sub-FSM cancelled, we cancel too'
        fsmStack.pop(request, eventName='exceptCancel') # cancel this FSM
        return edge.toNode
    
def deploy(modname, username, prefix='ct.fsm_plugin.'):
    'load FSM specifications found in the specified plugin module'
    import importlib
    mod = importlib.import_module(prefix + modname)
    l = []
    for fsmSpec in mod.get_specs():
        l.append(fsmSpec.save_graph(username))
    return l

def deploy_all(username, ignore=('testme', '__init__', 'fsmspec'),
               pattern='ct/fsm_plugin/*.py'):
    'load all FSM specifications found via pattern but not ignore'
    import glob
    import os.path
    l = []
    for modpath in glob.glob(pattern):
        modname = os.path.basename(modpath)[:-3]
        if modname not in ignore:
            l += deploy(modname, username)
    return l
