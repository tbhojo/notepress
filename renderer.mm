// SPDX-License-Identifier: MIT
// Uses private selectors and an outline-container ABI from the installed PencilKit.
// See ACKNOWLEDGMENTS.md and README.md before changing compatibility claims.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <PencilKit/PencilKit.h>
#import <CoreGraphics/CoreGraphics.h>
#include <vector>
#include <cstdint>
#include <utility>
#include <cmath>
struct OutlinePoint {int64_t x,y; double auxiliary;};
using Outlines=std::vector<std::vector<OutlinePoint>>;
@interface PKStroke (ReadOnlyOutline)
-(void*)newOutlinePaths;
-(BOOL)_isHidden;
@end
@interface PKInk (ReadOnlyBehavior)
-(id)behavior;
@end
@interface NSObject (ReadOnlyBehavior)
-(double)alphaForStroke:(PKStroke*)stroke;
@end
// Simplify outline polygons with a maximum 0.02 source-point deviation.
// This retains far more geometric detail than the original screen rendering.
static std::vector<OutlinePoint> simplify(const std::vector<OutlinePoint>&input){
 if(input.size()<5)return input;
 std::vector<OutlinePoint>points=input;points.push_back(input[0]);
 std::vector<bool>keep(points.size(),false);keep[0]=keep.back()=true;
 std::vector<std::pair<size_t,size_t>>stack{{0,points.size()-1}};
 while(!stack.empty()){
  auto [a,b]=stack.back();stack.pop_back();if(b<=a+1)continue;
  double dx=points[b].x-points[a].x,dy=points[b].y-points[a].y,len2=dx*dx+dy*dy;
  double best=4.0;size_t index=0;
  for(size_t j=a+1;j<b;j++){
   double x=points[j].x-points[a].x,y=points[j].y-points[a].y;
   double t=len2?fmin(1.0,fmax(0.0,(x*dx+y*dy)/len2)):0;
   double ex=x-t*dx,ey=y-t*dy,d2=ex*ex+ey*ey;
   if(d2>best){best=d2;index=j;}
  }
  if(index){keep[index]=true;stack.push_back({a,index});stack.push_back({index,b});}
 }
 std::vector<OutlinePoint>out;
 for(size_t j=0;j<input.size();j++)if(keep[j])out.push_back(input[j]);
 return out.size()>2?out:input;
}
static CGPathRef strokeOutline(PKStroke*s,size_t&vertices){
 Outlines*paths=(Outlines*)[s newOutlinePaths];
 CGMutablePathRef p=CGPathCreateMutable();
 for(const auto&rawPolygon:*paths){
  auto polygon=simplify(rawPolygon);
  if(polygon.empty())continue;
  CGPathMoveToPoint(p,nullptr,polygon[0].x/100.0,polygon[0].y/100.0);
  for(size_t k=1;k<polygon.size();k++)CGPathAddLineToPoint(p,nullptr,polygon[k].x/100.0,polygon[k].y/100.0);
  CGPathCloseSubpath(p);vertices+=polygon.size();
 }
 delete paths;return p;
}
int main(int argc,char**argv){@autoreleasepool{
 if(argc<4||argc>5){fprintf(stderr,"Usage: renderer DRAWING CANVAS_WIDTH OUTPUT_PDF [TITLE]\n");return 2;}
 setbuf(stdout,nullptr);
 NSError*err=nil;
 PKDrawing*d=[[PKDrawing alloc]initWithData:[NSData dataWithContentsOfFile:@(argv[1])] error:&err];
 if(!d){NSLog(@"Decode failed %@",err);return 1;}
 if(![PKStroke instancesRespondToSelector:@selector(newOutlinePaths)]||![PKStroke instancesRespondToSelector:@selector(_isHidden)]){fprintf(stderr,"This PencilKit version is unsupported\n");return 1;}
 NSArray<PKStroke*>*strokes=d.strokes;
 printf("Loaded %lu strokes\n",strokes.count);
 std::vector<CGRect>bounds;bounds.reserve(strokes.count);
 unsigned hidden=0,masked=0;
 for(PKStroke*s in strokes){bounds.push_back(s.renderBounds);hidden+=s._isHidden;masked+=(s.mask!=nil);}
 double paperWidth=atof(argv[2]);
 if(!(paperWidth>0&&paperWidth<100000)){fprintf(stderr,"Invalid canvas width\n");return 1;}
 NSInteger height=(NSInteger)ceil(CGRectGetMaxY(d.bounds))+3;
 if(height<=0||height>10000000){fprintf(stderr,"Unsupported canvas height\n");return 1;}
 std::vector<int>delta(height+2,0),occupied(height+1,0);
 for(size_t i=0;i<strokes.count;i++)if(!strokes[i]._isHidden){
  NSInteger a=std::max<NSInteger>(0,floor(CGRectGetMinY(bounds[i]))-1);
  NSInteger b=std::min<NSInteger>(height,ceil(CGRectGetMaxY(bounds[i]))+1);
  if(b>a){delta[a]++;delta[b]--;}
 }
 int active=0;for(NSInteger y=0;y<height;y++){active+=delta[y];occupied[y]=active;}
 NSInteger maxHeight=std::max<NSInteger>(100,floor(720*paperWidth/540));
 std::vector<NSInteger>cuts{0};
 while(height-cuts.back()>maxHeight){
  NSInteger start=cuts.back(),lo=start+(NSInteger)(maxHeight*.72),hi=start+maxHeight;
  NSInteger cut=-1;double best=-1e20;
  for(NSInteger y=lo;y<=hi;){
   if(occupied[y]){y++;continue;}
   NSInteger a=y;while(y<=hi&&!occupied[y])y++;
   NSInteger middle=(a+y-1)/2;
   double score=2*(y-a)-.15*fabs(middle-(start+maxHeight*.96));
   if(score>best){best=score;cut=middle;}
  }
  if(cut<0){cut=lo;for(NSInteger y=lo+1;y<=hi;y++)if(occupied[y]<occupied[cut]||(occupied[y]==occupied[cut]&&y>cut))cut=y;}
  if(cut<=start){fprintf(stderr,"Cannot paginate this canvas\n");return 1;}
  cuts.push_back(cut);
 }
 cuts.push_back(height);
 NSMutableArray*pages=[NSMutableArray array];
 for(size_t i=0;i+1<cuts.size();i++){
  NSInteger a=cuts[i],b=cuts[i+1];
  NSInteger visibleA=std::max<NSInteger>(0,a-(a&&occupied[a]?16:0));
  NSInteger visibleB=std::min<NSInteger>(height,b+(b<height&&occupied[b]?16:0));
  [pages addObject:@{@"visible_start":@(visibleA),@"visible_end":@(visibleB)}];
 }
 printf("Visible strokes %lu; erasure masks %u; pages %lu\n",strokes.count-hidden,masked,pages.count);
 __block std::vector<bool>covered(strokes.count,false);
 CGRect media=CGRectMake(0,0,612,792);
 CFURLRef url=(__bridge CFURLRef)[NSURL fileURLWithPath:@(argv[3])];
 NSDictionary*info=@{(__bridge NSString*)kCGPDFContextTitle:(argc>4?@(argv[4]):@"Apple Notes"),(__bridge NSString*)kCGPDFContextCreator:@"NotePress"};
 CGContextRef ctx=CGPDFContextCreateWithURL(url,&media,(__bridge CFDictionaryRef)info);
 if(!ctx)return 1;
 __block unsigned pageNumber=0;__block size_t allVertices=0;
 [[NSAppearance appearanceNamed:NSAppearanceNameAqua] performAsCurrentDrawingAppearance:^{
 for(NSDictionary*page in pages){@autoreleasepool{
  double start=[page[@"visible_start"]doubleValue];
  double end=[page[@"visible_end"]doubleValue];
  double xmin=0,xmax=paperWidth;
  for(size_t i=0;i<strokes.count;i++)if(!strokes[i]._isHidden&&CGRectGetMaxY(bounds[i])>=start&&CGRectGetMinY(bounds[i])<=end){xmin=fmin(xmin,CGRectGetMinX(bounds[i]));xmax=fmax(xmax,CGRectGetMaxX(bounds[i]));}
  xmin=floor(xmin)-2;xmax=ceil(xmax)+2;
  double scale=fmin(540/(xmax-xmin),720/(end-start));
  CGPDFContextBeginPage(ctx,nullptr);
  CGContextSaveGState(ctx);
  CGContextTranslateCTM(ctx,36+(540-(xmax-xmin)*scale)/2,756);
  CGContextScaleCTM(ctx,scale,-scale);
  CGContextTranslateCTM(ctx,-xmin,-start);
  CGContextClipToRect(ctx,CGRectMake(xmin,start,xmax-xmin,end-start));
  size_t count=0,vertices=0;
  for(size_t i=0;i<strokes.count;i++){
   PKStroke*s=strokes[i];
   if(s._isHidden||CGRectGetMaxY(bounds[i])<start||CGRectGetMinY(bounds[i])>end)continue;
   CGContextSaveGState(ctx);
   if(s.mask){
    CGAffineTransform transform=s.transform;
    CGPathRef mask=CGPathCreateCopyByTransformingPath(s.mask.CGPath,&transform);
    CGContextAddPath(ctx,mask);CGContextEOClip(ctx);CGPathRelease(mask);
   }
   CGPathRef path=strokeOutline(s,vertices);
   NSColor*color=[s.ink.color colorUsingColorSpace:[NSColorSpace sRGBColorSpace]];
   CGFloat r,g,b,a;[color getRed:&r green:&g blue:&b alpha:&a];
   double alpha=[s.ink.behavior alphaForStroke:s];
   CGContextSetRGBFillColor(ctx,r,g,b,fmin(1,fmax(0,alpha)));
   CGContextAddPath(ctx,path);CGContextEOFillPath(ctx);CGPathRelease(path);
   CGContextRestoreGState(ctx);count++;covered[i]=true;
  }
  CGContextRestoreGState(ctx);CGPDFContextEndPage(ctx);
  printf("Page %u strokes %zu vertices %zu x %.0f..%.0f y %.2f..%.2f scale %.4f\n",++pageNumber,count,vertices,xmin,xmax,start,end,scale);
  allVertices+=vertices;
 }}
 }];
 CGPDFContextClose(ctx);CGContextRelease(ctx);
 for(size_t i=0;i<strokes.count;i++)if(!strokes[i]._isHidden&&!covered[i]){fprintf(stderr,"ERROR: uncovered stroke %zu\n",i);return 1;}
 CGPDFDocumentRef result=CGPDFDocumentCreateWithURL(url);
 if(!result||CGPDFDocumentGetNumberOfPages(result)!=pageNumber){fprintf(stderr,"ERROR: PDF verification failed\n");return 1;}
 CGPDFDocumentRelease(result);
 printf("Verified %u pages and every visible stroke; %zu vector vertices\n",pageNumber,allVertices);
 return 0;
}}
