"""Shorten printed generic configuration literals without dropping CPU rows."""
def compact_report(report):
    parts=[];start=0
    while True:
        at=report.find('.{',start)
        if at<0:
            parts.append(report[start:]);break
        depth=0;quoted=False;escaped=False;end=at+1
        while end<len(report):
            char=report[end]
            if quoted:
                if escaped: escaped=False
                elif char=='\\': escaped=True
                elif char=='"': quoted=False
            elif char=='"': quoted=True
            elif char=='{': depth+=1
            elif char=='}':
                depth-=1
                if depth==0: break
            end+=1
        if depth!=0:
            parts.append(report[start:]);break
        parts.extend((report[start:at],'config'));start=end+1
    lines=[]
    for line in ''.join(parts).splitlines():
        if line.startswith('# ....'): continue
        line=' '.join(line.split())
        # Preserve every row's percentage even for other oversized type names.
        if len(line)>600: line=line[:550]+' ... [full symbol in self-full.txt.gz]'
        lines.append(line)
    return '\n'.join(lines)+'\n'
