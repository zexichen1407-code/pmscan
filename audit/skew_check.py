import pickle, statistics, math
with open(r"C:\Users\zexi\pmscan\audit\twosided_stream.pkl","rb") as f:
    sel=pickle.load(f)

def pearson(x,y):
    n=len(x)
    if n<3: return float('nan')
    mx=sum(x)/n; my=sum(y)/n
    cov=sum((a-mx)*(b-my) for a,b in zip(x,y))
    vx=sum((a-mx)**2 for a in x); vy=sum((b-my)**2 for b in y)
    if vx==0 or vy==0: return float('nan')
    return cov/math.sqrt(vx*vy)

# Spearman (rank) version, robust to outliers/scale
def spearman(x,y):
    def rank(v):
        order=sorted(range(len(v)),key=lambda i:v[i])
        r=[0]*len(v)
        i=0
        while i<len(v):
            j=i
            while j+1<len(v) and v[order[j+1]]==v[order[i]]: j+=1
            avg=(i+j)/2.0+1
            for k in range(i,j+1): r[order[k]]=avg
            i=j+1
        return r
    return pearson(rank(x),rank(y))

# Build normalized imbalance vs next-buy direction; also vs next-buy PRICE-on-axis
xs=[]; yd=[]; yp=[]
for cid,c in sel.items():
    fills=sorted(c["fills"]); scale=max(c["b0s"],c["b1s"],1)
    inv0=inv1=0.0; first=True
    for (ts,oi,side,pr,sz) in fills:
        if side=="BUY":
            if not first:
                xs.append((inv0-inv1)/scale)
                yd.append(1.0 if oi==0 else -1.0)
                yp.append(pr if oi==0 else -pr)
            first=False
            if oi==0: inv0+=sz
            else: inv1+=sz
        else:
            if oi==0: inv0-=sz
            else: inv1-=sz

print("Pearson  corr(norm_imbalance, next_dir +yes/-no) =", round(pearson(xs,yd),4))
print("Spearman corr(norm_imbalance, next_dir)          =", round(spearman(xs,yd),4))
print("Pearson  corr(norm_imbalance, next signed price) =", round(pearson(xs,yp),4))
print("n =", len(xs))

# Conditional: when long-yes (x>0) what % of next buys are yes vs no?
posx=[d for x,d in zip(xs,yd) if x>0.02]
negx=[d for x,d in zip(xs,yd) if x<-0.02]
def shareyes(L): return sum(1 for d in L if d>0)/len(L) if L else float('nan')
print(f"\nWhen LONG yes (imb>+2% scale): next-buy is YES {shareyes(posx)*100:.1f}% of time (n={len(posx)})")
print(f"When LONG no  (imb<-2% scale): next-buy is YES {shareyes(negx)*100:.1f}% of time (n={len(negx)})")
print("--> If he REBALANCES, long-yes should lead to mostly NO buys (low %yes).")
print("--> If he CONTINUES building the cheap leg, long-cheap-leg buys keep going same side.")
