"""Audited offline fixture; selected only by an exact statement hash."""
from __future__ import annotations

STANDARD = r'''#include <bits/stdc++.h>
using namespace std;
int main() {
    ios::sync_with_stdio(false); cin.tie(nullptr);
    int n; if (!(cin >> n)) return 1;
    long long best=LLONG_MIN, ending=0;
    for(int i=0;i<n;++i) {
        long long x; if (!(cin>>x)) return 2;
        ending = i == 0 ? x : max(x, ending+x);
        best=max(best,ending);
    }
    cout<<best<<'\n';
}
'''

BRUTE = r'''#include <bits/stdc++.h>
using namespace std;
int main() {
    int n; if (!(cin>>n) || n<1 || n>100) return 1;
    vector<long long>a(n); for(auto &x:a) if(!(cin>>x)) return 2;
    long long ans=LLONG_MIN;
    for(int l=0;l<n;++l) {
        long long sum=0;
        for(int r=l;r<n;++r) { sum+=a[r]; ans=max(ans,sum); }
    }
    cout<<ans<<'\n';
}
'''

GENERATOR = r'''"""Deterministic maximum subarray data. No network or third-party modules."""
import random

CASE_COVERAGE={1:['最小规模','全负'],2:['最大规模','全等正数','32位溢出'],3:['最大规模','全负'],4:['最大规模','全零'],5:['最大规模','单调'],6:['最大规模','单调'],7:['最大规模','交替极值'],8:['最大规模'],9:['最大规模','前后缀最优'],10:['最大规模','前后缀最优'],11:[],12:[],13:['最小规模'],14:['最小规模','全零'],15:['全等正数'],16:['最大规模','全负','随机'],17:['最大规模','随机']}

def encode(a, method):
    return str(len(a))+"\n"+" ".join(map(str,a))+"\n", {"method":method,"n":len(a)}

def make_case(rng, index, points):
    n=200000
    if index==1: return encode([-10**9],"最小规模，单个最小负数；卡空子段")
    if index==2: return encode([10**9]*n,"最大规模，全相等正数；卡32位溢出")
    if index==3: return encode([-10**9]*n,"最大规模，全相等负数")
    if index==4: return encode([0]*n,"最大规模，全零")
    if index==5: return encode(list(range(-100000,100000)),"最大规模，严格递增")
    if index==6: return encode(list(range(100000,-100000,-1)),"最大规模，严格递减")
    if index==7: return encode([10**9,-10**9]*(n//2),"最大规模，极值交替")
    if index==8: return encode([-10**9]*(n//2)+[1]+[-10**9]*(n//2-1),"唯一正数夹在极小值中")
    if index==9: return encode([10**9]*(n//2)+[-10**9]*(n//2),"最优子段在前缀")
    if index==10: return encode([-10**9]*(n//2)+[10**9]*(n//2),"最优子段在后缀")
    if index==11: return encode([5,-1,5,-100,6,6],"多个局部最优；卡错误重置")
    if index==12: return encode([-1,0,-1],"最优子段为单个零")
    if index==13: return encode([10**9],"单个最大正数")
    if index==14: return encode([0],"单个零")
    if index==15: return encode([7]*20000,"中规模，全相等正数")
    if index==16: return encode([rng.randint(-10**9,-1) for _ in range(n)],"最大规模，随机全负")
    if index==17: return encode([rng.choice([-10**9,0,10**9]) for _ in range(n)],"最大规模，随机极值")
    size=n if index%2==0 else rng.randint(2,10000)
    return encode([rng.randint(-10**9,10**9) for _ in range(size)],"均匀随机混合正负")

def generate(rng,index,points):
    text,meta=make_case(rng,index,points)
    meta['coverage']=CASE_COVERAGE.get(index,['随机'])
    return text,meta

def small(rng,index):
    edges=[[-1],[0],[1],[-10**9],[10**9],[-2,-1],[10**9,10**9],[-1,3,-1,3,-9]]
    if index<len(edges): return encode(edges[index],"小规模边界")
    return encode([rng.randint(-10**9,10**9) for _ in range(rng.randint(1,35))],"小规模随机暴力对拍")

if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--index',type=int,default=1)
    p.add_argument('--points',type=int,default=20)
    a=p.parse_args()
    print(generate(random.Random(a.seed),a.index,a.points)[0],end='')
'''

VALIDATOR = r'''import re

def integers(text):
    ts=text.split()
    if any(not re.fullmatch(r'-?(0|[1-9][0-9]*)',t) for t in ts):
        raise ValueError("输入/输出必须是规范十进制整数")
    return list(map(int,ts))

def validate_input(text):
    lines=text.splitlines()
    if len(lines)!=2 or len(lines[0].split())!=1:
        raise ValueError("输入必须恰好两行")
    values=integers(text)
    if not values: raise ValueError("空输入")
    n=values[0]
    if not 1<=n<=200000 or len(values)!=n+1:
        raise ValueError("n 范围或元素数量错误")
    if any(not -10**9<=v<=10**9 for v in values[1:]):
        raise ValueError("元素越界")
    return {"n":n,"min":min(values[1:]),"max":max(values[1:])}

def validate_output(input_text,output_text):
    vals=integers(output_text)
    if len(vals)!=1 or not -10**9<=vals[0]<=200000*10**9:
        raise ValueError("输出应为范围合法的单个整数")
    if not output_text.endswith("\n"):
        raise ValueError("输出缺少换行")

def validate_coverage(text,tags):
    a=integers(text)[1:]
    checks={
        '最小规模':len(a)==1,'最大规模':len(a)==200000,
        '全负':all(x<0 for x in a),'全零':all(x==0 for x in a),
        '全等正数':a[0]>0 and len(set(a))==1,
        '单调':all(a[i]<=a[i+1] for i in range(len(a)-1)) or all(a[i]>=a[i+1] for i in range(len(a)-1)),
        '交替极值':all(x==10**9*(-1)**i for i,x in enumerate(a)),
        '32位溢出':sum(x for x in a if x>0)>2**31-1,
        '前后缀最优':len(a)>1 and (all(x>0 for x in a[:len(a)//2]) and all(x<0 for x in a[len(a)//2:]) or all(x<0 for x in a[:len(a)//2]) and all(x>0 for x in a[len(a)//2:])),
        '随机':len(set(a))>1,
    }
    if any(t not in checks or not checks[t] for t in tags):
        raise ValueError('覆盖标签与实际数据不符')
'''


def bundle(statement: str, points: int = 20) -> dict:
    namespace={}
    exec(GENERATOR,namespace)
    coverage=sorted({tag for i in range(1,points+1) for tag in namespace['CASE_COVERAGE'].get(i,['随机'])})
    evidence = '$1 \\le n \\le 200000$，$-10^9 \\le a_i \\le 10^9$。'
    return {
        'spec': {
            'title': '最大连续子段和', 'ambiguities': [],
            'input_format': '第一行 n，第二行 n 个整数',
            'output_format': '唯一最大和，一个整数',
            'constraints': [{'description': '1<=n<=200000; -1e9<=a_i<=1e9', 'evidence': evidence}],
            'unique_output': True, 'checker': 'text',
            'algorithm': 'Kadane，O(n) 时间、O(1) 额外空间；64 位有符号整数。',
            'proof': 'ending 为以当前位置结尾的非空子段最大和；它只能单独取当前项或扩展上一项的最优子段。对所有 ending 取最大。',
            'brute_algorithm': '独立枚举左右端点，O(n²) 累加求和，n<=35。',
            'time_ms': 1000, 'memory_mb': 256,
            'limits_source': '题面资源限制；实际洛谷机器速度可能不同。',
            'subtasks': [],
            'wrong_solutions': [{'file':'wrong_1.cpp','description':'错误允许空子段，全负时返回0'}, {'file':'wrong_2.cpp','description':'答案错误使用32位整数导致溢出'}],
            'coverage': coverage,
            'risks': ['有限对拍不是形式化正确性证明；示例未在洛谷评测机运行。']
        },
        'sources': {'standard.cpp': STANDARD, 'brute.cpp': BRUTE, 'generator.py': GENERATOR, 'validator.py': VALIDATOR, 'wrong_1.cpp': STANDARD.replace('cout<<best', 'best=max(0LL,best); cout<<best'), 'wrong_2.cpp': STANDARD.replace("cout<<best", "cout<<static_cast<int>(best)")},
        'review': {'approved': True, 'issues': [], 'method': '内置人工检查的固定示例，标准解与暴力采用不同算法'}
    }
